import 'dart:async';
import 'dart:convert';
import 'dart:io';

import '../net/resilient_http.dart';
import 'api_config.dart';

/// Ошибка обращения к мобильному гейтвею.
class ApiException implements Exception {
  ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => 'ApiException($statusCode): $message';
}

class _TransportFailure implements Exception {
  const _TransportFailure(this.cause);
  final Object cause;
}

/// Минимальный HTTP-клиент поверх dart:io.
///
/// Важное свойство для телефона: IP и маршрут могут поменяться прямо во
/// время работы (Wi‑Fi → LTE, VPN on/off, смена VPN-гео). Поэтому безопасные
/// запросы при сетевом обрыве повторяются с новым HttpClient/сокетом, а при
/// наличии резервного HTTPS-маршрута пробуют и его. TLS не ослабляется.
class ApiClient {
  ApiClient({String? baseUrl, String? deviceToken, HttpClient? httpClient})
      : _explicitBaseUrl = baseUrl,
        _explicitToken = deviceToken,
        _ownsClient = httpClient == null,
        _client = httpClient ?? _newClient();

  final String? _explicitBaseUrl;
  final String? _explicitToken;
  final bool _ownsClient;
  HttpClient _client;

  static HttpClient _newClient() {
    final client = resilientHttpClient();
    client
      ..connectionTimeout = ApiConfig.requestTimeout
      // Долго живущий keep-alive чаще всего и ломается при переключении VPN.
      // Короткий idle timeout сохраняет reuse внутри серии запросов, но не
      // держит старый маршрут минутами.
      ..idleTimeout = const Duration(seconds: 12);
    return client;
  }

  String get _deviceToken => _explicitToken ?? ApiConfig.deviceToken;

  /// Основной адрес, по которому клиент ходит первым.
  String get baseUrl => _explicitBaseUrl ?? ApiConfig.baseUrl;

  List<String> get _routes {
    if (_explicitBaseUrl != null) return [_explicitBaseUrl];
    return ApiConfig.endpoints;
  }

  Future<Map<String, dynamic>> get(String path) async =>
      await _send('GET', path) as Map<String, dynamic>;

  Future<List<dynamic>> getList(String path) async =>
      await _send('GET', path) as List<dynamic>;

  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async =>
      await _send(
        'POST',
        path,
        body: body,
        idempotencyKey: idempotencyKey,
      )
          as Map<String, dynamic>;

  /// Pairing is deliberately a separate transport capability instead of a
  /// generic caller-supplied header. This keeps ordinary ApiClient.post
  /// implementations substitutable and prevents unrelated features from
  /// attaching the short-lived pairing capability to arbitrary requests.
  Future<Map<String, dynamic>> postForPairing(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    required String pairingSessionId,
  }) async =>
      await _send(
        'POST',
        path,
        body: body,
        idempotencyKey: idempotencyKey,
        pairingSessionId: pairingSessionId,
      )
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> put(
    String path, {
    Map<String, dynamic>? body,
  }) async => await _send('PUT', path, body: body) as Map<String, dynamic>;

  Future<Map<String, dynamic>> patch(
    String path, {
    Map<String, dynamic>? body,
  }) async => await _send('PATCH', path, body: body) as Map<String, dynamic>;

  Future<Map<String, dynamic>> delete(String path) async =>
      await _send('DELETE', path) as Map<String, dynamic>;

  Future<Object?> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    String? pairingSessionId,
  }) async {
    final routes = _routes.where((e) => e.trim().isNotEmpty).toList();
    if (routes.isEmpty) {
      throw ApiException('Адрес движка не задан.');
    }
    for (final route in routes) {
      final uri = Uri.tryParse('$route$path');
      if (uri == null || !uri.isScheme('https')) {
        throw ApiException('Гейтвей должен быть доступен только по HTTPS: $route');
      }
    }

    // GET/PUT/DELETE безопасно повторять. POST повторяем только при наличии
    // idempotency key: сетевой обрыв после принятого POST не должен создать
    // вторую сделку. PATCH без отдельного ключа не повторяем автоматически.
    final retryable = method == 'GET' ||
        method == 'PUT' ||
        method == 'DELETE' ||
        (method == 'POST' && idempotencyKey != null);
    final passes = retryable ? 2 : 1;
    Object? lastFailure;

    for (var pass = 0; pass < passes; pass++) {
      for (final route in routes) {
        try {
          return await _sendOnce(
            method,
            route,
            path,
            body: body,
            idempotencyKey: idempotencyKey,
            pairingSessionId: pairingSessionId,
          );
        } on _TransportFailure catch (failure) {
          lastFailure = failure.cause;
          if (!retryable) {
            throw ApiException('Нет связи с сервером: ${failure.cause}');
          }
          _resetOwnedClient();
        }
      }
      if (pass + 1 < passes) {
        await Future<void>.delayed(Duration(milliseconds: 250 * (pass + 1)));
      }
    }

    throw ApiException('Нет связи с сервером: ${lastFailure ?? 'маршрут недоступен'}');
  }

  Future<Object?> _sendOnce(
    String method,
    String route,
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    String? pairingSessionId,
  }) async {
    final uri = Uri.parse('$route$path');
    final HttpClientRequest request;
    try {
      request = await _client
          .openUrl(method, uri)
          .timeout(ApiConfig.requestTimeout);
    } on SocketException catch (e) {
      throw _TransportFailure(e);
    } on HandshakeException catch (e) {
      throw _TransportFailure(e);
    } on HttpException catch (e) {
      throw _TransportFailure(e);
    } on TimeoutException catch (e) {
      throw _TransportFailure(e);
    }

    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (_deviceToken.isNotEmpty) {
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_deviceToken');
    }
    if (idempotencyKey != null) {
      request.headers.set('X-Idempotency-Key', idempotencyKey);
    }
    // This is deliberately a narrow argument rather than a generic caller
    // header map: no feature may override the bearer or TLS-safe transport
    // controls. The value exists only for this request and is never logged.
    if (pairingSessionId != null) {
      request.headers.set('X-Pairing-Session-Id', pairingSessionId);
    }
    if (body != null) {
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(body));
    }

    late final HttpClientResponse response;
    late final String text;
    try {
      response = await request.close().timeout(ApiConfig.requestTimeout);
      text = await response
          .transform(utf8.decoder)
          .join()
          .timeout(ApiConfig.requestTimeout);
    } on SocketException catch (e) {
      throw _TransportFailure(e);
    } on HandshakeException catch (e) {
      throw _TransportFailure(e);
    } on HttpException catch (e) {
      throw _TransportFailure(e);
    } on TimeoutException catch (e) {
      throw _TransportFailure(e);
    }

    if (response.statusCode >= 400) {
      throw ApiException(
        _errorMessage(text, response.statusCode),
        statusCode: response.statusCode,
      );
    }
    if (text.isEmpty) return <String, dynamic>{};
    return jsonDecode(text) as Object?;
  }

  void _resetOwnedClient() {
    if (!_ownsClient) return;
    _client.close(force: true);
    _client = _newClient();
  }

  String _errorMessage(String text, int status) {
    try {
      final decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) {
        final error = decoded['error'];
        if (error is Map<String, dynamic> && error['message'] is String) {
          return error['message'] as String;
        }
        if (decoded['message'] is String) return decoded['message'] as String;
        if (decoded['detail'] is String) return decoded['detail'] as String;
      }
    } on FormatException {
      // тело не JSON — покажем статус
    }
    return switch (status) {
      401 || 403 => 'Устройство не авторизовано. Пройдите привязку заново.',
      409 => 'Сигнал больше не актуален.',
      _ => 'Сервер вернул ошибку $status',
    };
  }

  void close() => _client.close(force: true);
}
