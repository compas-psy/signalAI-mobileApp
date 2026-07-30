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

/// Минимальный HTTP-клиент поверх dart:io.
///
/// Внешних зависимостей нет намеренно: чем меньше стороннего кода в приложении,
/// которое подтверждает сделки, тем лучше (ТЗ §11).
class ApiClient {
  ApiClient({String? baseUrl, String? deviceToken, HttpClient? httpClient})
      : _explicitBaseUrl = baseUrl,
        _deviceToken = deviceToken ?? ApiConfig.deviceToken,
        _client = httpClient ?? resilientHttpClient() {
    _client.connectionTimeout = ApiConfig.requestTimeout;
  }

  /// Адрес, заданный этому клиенту явно (тесты, особые случаи).
  ///
  /// null — берём общий адрес приложения. Читается **на каждом запросе**, а
  /// не запоминается в конструкторе: адрес движка теперь меняется из
  /// «Подключений», а клиент живёт всё время работы приложения. Запомни его
  /// один раз — и после смены адреса запросы продолжили бы уходить на
  /// старый сервер до перезапуска.
  final String? _explicitBaseUrl;
  final String _deviceToken;
  final HttpClient _client;

  /// Адрес, по которому клиент реально ходит.
  ///
  /// Нужен снаружи, потому что «настроен ли движок» — свойство **этого**
  /// клиента, а не глобальной константы сборки. Пока проверка смотрела в
  /// `ApiConfig`, подставить клиент в тесте было нельзя: с пустой константой
  /// любой запрос обрывался до подстановки.
  String get baseUrl => _explicitBaseUrl ?? ApiConfig.baseUrl;

  // `await` здесь обязателен, и это не стиль. Без него приведение типа
  // применяется к самому `Future`, а не к его результату, и любой запрос
  // падает с «type 'Future<Object?>' is not a subtype of type
  // 'Map<String, dynamic>'». Дефект пролежал незамеченным всё время, пока
  // REST-клиент ни разу не вызывался, и вылез первым же обращением к движку.
  Future<Map<String, dynamic>> get(String path) async =>
      await _send('GET', path) as Map<String, dynamic>;

  /// Ответ-массив. Контракт §18 отдаёт ленту идей списком верхнего уровня,
  /// и приводить её к объекту на стороне сервера значит менять контракт под
  /// удобство одного клиента.
  Future<List<dynamic>> getList(String path) async =>
      await _send('GET', path) as List<dynamic>;

  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async =>
      await _send('POST', path, body: body, idempotencyKey: idempotencyKey)
          as Map<String, dynamic>;

  Future<Map<String, dynamic>> patch(String path, {Map<String, dynamic>? body}) async =>
      await _send('PATCH', path, body: body) as Map<String, dynamic>;

  Future<Object?> _send(
    String method,
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    final uri = Uri.parse('$baseUrl$path');
    if (!uri.isScheme('https')) {
      throw ApiException('Гейтвей должен быть доступен только по HTTPS: $uri');
    }

    final HttpClientRequest request;
    try {
      request = await _client.openUrl(method, uri).timeout(ApiConfig.requestTimeout);
    } on Exception catch (e) {
      throw ApiException('Нет связи с сервером: $e');
    }

    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (_deviceToken.isNotEmpty) {
      request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_deviceToken');
    }
    if (idempotencyKey != null) {
      // Повтор команды не должен создавать второй ордер (ТЗ §7).
      request.headers.set('X-Idempotency-Key', idempotencyKey);
    }
    if (body != null) {
      request.headers.contentType = ContentType.json;
      request.write(jsonEncode(body));
    }

    final response = await request.close().timeout(ApiConfig.requestTimeout);
    final text = await response.transform(utf8.decoder).join();

    if (response.statusCode >= 400) {
      throw ApiException(
        _errorMessage(text, response.statusCode),
        statusCode: response.statusCode,
      );
    }
    if (text.isEmpty) return <String, dynamic>{};
    return jsonDecode(text) as Object?;
  }

  /// Сервер возвращает {"error": {"message": "…"}} — показываем человеку суть,
  /// а не голый код (ТЗ §7: понятный текст ошибки).
  String _errorMessage(String text, int status) {
    try {
      final decoded = jsonDecode(text);
      if (decoded is Map<String, dynamic>) {
        final error = decoded['error'];
        if (error is Map<String, dynamic> && error['message'] is String) {
          return error['message'] as String;
        }
        if (decoded['message'] is String) return decoded['message'] as String;
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
