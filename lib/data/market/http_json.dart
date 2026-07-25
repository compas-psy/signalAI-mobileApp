import 'dart:convert';
import 'dart:io';

/// Ошибка загрузки рыночных данных.
class MarketDataException implements Exception {
  MarketDataException(this.message, {this.retryable = true});

  final String message;

  /// Имеет ли смысл повторять запрос. Неверный символ или сломанный формат
  /// ответа не починятся повтором — такие ошибки поднимаются сразу.
  final bool retryable;

  @override
  String toString() => 'MarketDataException: $message';
}

/// Минимальный GET-JSON поверх dart:io с ретраями.
///
/// Используется для публичных эндпоинтов MOEX ISS и Bybit — авторизации там нет,
/// поэтому и клиент простой (ТЗ §3: публичные данные бесплатны и без ключа).
class HttpJson {
  HttpJson({HttpClient? client, this.timeout = const Duration(seconds: 15)})
      : _client = client ?? HttpClient() {
    _client.connectionTimeout = timeout;
    _client.userAgent = 'SignalAI/1.0 (personal)';
  }

  final HttpClient _client;
  final Duration timeout;

  /// GET с экспоненциальной паузой между попытками (ТЗ §3.3).
  ///
  /// Повторяются только те ответы, которые имеет смысл повторять: 5xx и 429.
  /// Остальные 4xx — это ошибка запроса (не тот символ, не та колонка), она не
  /// пройдёт и с третьей попытки, поэтому бросается сразу и с кодом в тексте.
  Future<Map<String, dynamic>> get(Uri uri, {int attempts = 3}) async {
    Object? lastError;
    for (var attempt = 0; attempt < attempts; attempt++) {
      if (attempt > 0) {
        await Future<void>.delayed(Duration(milliseconds: 400 * (1 << (attempt - 1))));
      }
      try {
        final request = await _client.getUrl(uri).timeout(timeout);
        request.headers.set(HttpHeaders.acceptHeader, 'application/json');
        final response = await request.close().timeout(timeout);
        final body = await response.transform(utf8.decoder).join();
        if (response.statusCode >= 400) {
          final code = response.statusCode;
          // 429 и 5xx — временные, остальные 4xx повторять бессмысленно.
          final retryable = code == 429 || code >= 500;
          final error = MarketDataException(
            '${uri.host} ответил $code',
            retryable: retryable,
          );
          if (!retryable) throw error;
          lastError = error;
          continue;
        }
        final decoded = jsonDecode(body);
        if (decoded is! Map<String, dynamic>) {
          throw MarketDataException(
            '${uri.host}: неожиданный формат ответа',
            retryable: false,
          );
        }
        return decoded;
      } on MarketDataException catch (e) {
        if (!e.retryable) rethrow;
        lastError = e;
      } on Exception catch (e) {
        lastError = e;
      }
    }
    throw MarketDataException('Не удалось получить данные с ${uri.host}: $lastError');
  }

  void close() => _client.close(force: true);
}

/// Разбор блока ISS вида `{"columns": [...], "data": [[...], ...]}`
/// в список карт «колонка → значение».
List<Map<String, Object?>> issRows(Map<String, dynamic> json, String block) {
  final section = json[block];
  if (section is! Map<String, dynamic>) return const [];
  final columns = (section['columns'] as List<dynamic>? ?? const []).cast<String>();
  final data = section['data'] as List<dynamic>? ?? const [];
  return [
    for (final row in data)
      {
        for (var i = 0; i < columns.length && i < (row as List).length; i++)
          columns[i]: row[i] as Object?,
      },
  ];
}
