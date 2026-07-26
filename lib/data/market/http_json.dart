import 'dart:convert';
import 'dart:io';

import '../net/resilient_http.dart';
import 'net_failure.dart';

export 'net_failure.dart' show MarketDataException, NetFailureKind;

/// Минимальный GET-JSON поверх dart:io с ретраями и обходом сломанного DNS.
///
/// Используется для публичных эндпоинтов MOEX ISS и Bybit — авторизации там нет,
/// поэтому и клиент простой (ТЗ §3: публичные данные бесплатны и без ключа).
///
/// Если системный резолвер устройства отказал, соединение устанавливается на
/// адрес, полученный по DNS-over-HTTPS — этим занимается
/// [resilientHttpClient]. Проверка сертификата и SNI при этом остаются
/// полными и по настоящему имени хоста.
class HttpJson {
  HttpJson({HttpClient? client, this.timeout = const Duration(seconds: 15)})
      : _client = client ?? resilientHttpClient() {
    _client.connectionTimeout = timeout;
    _client.userAgent = 'SignalAI/1.0 (personal)';
  }

  final HttpClient _client;
  final Duration timeout;

  /// GET с экспоненциальной паузой между попытками (ТЗ §3.3).
  ///
  /// Повторяются только те отказы, которые имеет смысл повторять: 5xx, 429,
  /// таймауты и обрывы соединения. Ошибка запроса (не тот символ, не та
  /// колонка) и отказ резолвера повтором не лечатся — они поднимаются сразу.
  Future<Map<String, dynamic>> get(Uri uri, {int attempts = 3}) async {
    MarketDataException? lastError;
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
          final error = MarketDataException(
            '${uri.host} ответил $code',
            kind: code == 429 || code >= 500
                ? NetFailureKind.serverError
                : NetFailureKind.badRequest,
          );
          if (!error.retryable) throw error;
          lastError = error;
          continue;
        }
        final decoded = jsonDecode(body);
        if (decoded is! Map<String, dynamic>) {
          throw MarketDataException(
            '${uri.host}: неожиданный формат ответа',
            kind: NetFailureKind.badFormat,
          );
        }
        return decoded;
      } on Object catch (e) {
        final failure = MarketDataException.from(e, uri.host);
        if (!failure.retryable) throw failure;
        lastError = failure;
      }
    }
    throw lastError ?? MarketDataException('Не удалось получить данные с ${uri.host}');
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
