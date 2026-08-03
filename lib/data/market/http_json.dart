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

  /// Предел времени на весь [get] со всеми попытками.
  ///
  /// Считается от [timeout], а не задаётся отдельно: так один параметр
  /// по-прежнему управляет «терпением» клиента, но ретраи не могут
  /// растянуться на минуты. Приватный намеренно: тесты подменяют HttpJson
  /// через `implements`, и каждый публичный член ломал бы их компиляцию.
  Duration _deadlineFor(int attempts) => timeout * (attempts.clamp(1, 3) + 0.5);

  /// GET с экспоненциальной паузой между попытками (ТЗ §3.3).
  ///
  /// Повторяются только те отказы, которые имеет смысл повторять: 5xx, 429,
  /// таймауты и обрывы соединения. Ошибка запроса (не тот символ, не та
  /// колонка) и отказ резолвера повтором не лечатся — они поднимаются сразу.
  Future<Map<String, dynamic>> get(Uri uri, {int attempts = 3}) async {
    MarketDataException? lastError;
    // Общий дедлайн на всю операцию: без него три попытки по два таймаута
    // складывались в полторы минуты на один URL, а при 48 запросах на
    // пересчёт это часы.
    final deadline = DateTime.now().add(_deadlineFor(attempts));

    for (var attempt = 0; attempt < attempts; attempt++) {
      if (attempt > 0) {
        if (DateTime.now().isAfter(deadline)) break;
        await Future<void>.delayed(Duration(milliseconds: 400 * (1 << (attempt - 1))));
      }
      try {
        final request = await _client.getUrl(uri).timeout(timeout);
        request.headers.set(HttpHeaders.acceptHeader, 'application/json');
        final response = await request.close().timeout(timeout);
        // Таймаут именно здесь критичен: сервер может принять соединение,
        // отдать заголовки и замолчать на теле — типичное поведение мобильной
        // сети при шейпинге. Без таймаута этот await не завершается никогда,
        // и ретраи не спасают, потому что цикл до них не доходит.
        final body = await response
            .transform(utf8.decoder)
            .join()
            .timeout(timeout, onTimeout: () {
          // Соединение бросаем: держать его в пуле нет смысла.
          response.detachSocket().then((s) => s.destroy()).ignore();
          throw MarketDataException(
            '${uri.host} оборвал передачу данных',
            kind: NetFailureKind.timeout,
          );
        });
        if (response.statusCode >= 400) {
          // Тело обязано дойти до классификатора. Причина отказа лежит
          // именно там: CloudFront на 403 прямо пишет «block access from
          // your country», а сама по себе тройка 403 неотличима от «ключ
          // без прав» — и владелец идёт искать ошибку не туда.
          final error = MarketDataException.fromResponse(
            response.statusCode,
            uri.host,
            body: body,
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
        if (DateTime.now().isAfter(deadline)) break;
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
