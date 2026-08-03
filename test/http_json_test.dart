import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/market/http_json.dart';

/// Поднимает локальный сервер и отдаёт управление тесту.
Future<HttpServer> serve(void Function(HttpRequest request) handler) async {
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  server.listen(handler);
  return server;
}

void main() {
  test('сервер молчит на теле — запрос падает по таймауту, а не висит вечно', () async {
    // Ровно тот сценарий, что вешал экран диагностики: соединение принято,
    // заголовки отданы, тело не идёт. Раньше здесь был await без таймаута.
    final stalled = <HttpResponse>[];
    final server = await serve((request) {
      request.response.headers.contentType = ContentType.json;
      request.response.write('{"partial":');
      // Тело не закрываем: клиент должен сам оборвать ожидание.
      stalled.add(request.response);
    });
    addTearDown(() async {
      for (final r in stalled) {
        try {
          await r.close();
        } on Object {
          // соединение уже уничтожено клиентом
        }
      }
      await server.close(force: true);
    });

    final http = HttpJson(timeout: const Duration(milliseconds: 300));
    addTearDown(http.close);

    final stopwatch = Stopwatch()..start();
    await expectLater(
      http.get(Uri.parse('http://127.0.0.1:${server.port}/'), attempts: 1),
      throwsA(isA<MarketDataException>()),
    );
    stopwatch.stop();

    // Уложились в дедлайн: без исправления тест висел бы бесконечно.
    expect(stopwatch.elapsed, lessThan(const Duration(seconds: 5)));
  });

  test('дедлайн ограничивает суммарное время всех попыток', () async {
    final stalled = <HttpResponse>[];
    final server = await serve((request) {
      request.response.headers.contentType = ContentType.json;
      request.response.write('{');
      stalled.add(request.response);
    });
    addTearDown(() async {
      for (final r in stalled) {
        try {
          await r.close();
        } on Object {
          // соединение уже уничтожено
        }
      }
      await server.close(force: true);
    });

    final http = HttpJson(timeout: const Duration(milliseconds: 300));
    addTearDown(http.close);

    final stopwatch = Stopwatch()..start();
    await expectLater(
      http.get(Uri.parse('http://127.0.0.1:${server.port}/'), attempts: 3),
      throwsA(isA<MarketDataException>()),
    );
    stopwatch.stop();

    // 3 попытки × 300 мс + паузы, а не бесконечность.
    expect(stopwatch.elapsed, lessThan(const Duration(seconds: 6)));
  });

  test('нормальный JSON разбирается', () async {
    final server = await serve((request) {
      request.response.headers.contentType = ContentType.json;
      request.response.write('{"ok":true,"n":42}');
      request.response.close();
    });
    addTearDown(() => server.close(force: true));

    final http = HttpJson(timeout: const Duration(seconds: 3));
    addTearDown(http.close);

    final json = await http.get(Uri.parse('http://127.0.0.1:${server.port}/'));
    expect(json['ok'], isTrue);
    expect(json['n'], 42);
  });

  test('4xx не ретраится и поднимается сразу', () async {
    var hits = 0;
    final server = await serve((request) {
      hits++;
      request.response.statusCode = 404;
      request.response.close();
    });
    addTearDown(() => server.close(force: true));

    final http = HttpJson(timeout: const Duration(seconds: 3));
    addTearDown(http.close);

    await expectLater(
      http.get(Uri.parse('http://127.0.0.1:${server.port}/'), attempts: 3),
      throwsA(isA<MarketDataException>()),
    );
    expect(hits, 1, reason: '404 повтором не лечится');
  });

  test('5xx ретраится указанное число раз', () async {
    var hits = 0;
    final server = await serve((request) {
      hits++;
      request.response.statusCode = 503;
      request.response.close();
    });
    addTearDown(() => server.close(force: true));

    final http = HttpJson(timeout: const Duration(seconds: 3));
    addTearDown(http.close);

    await expectLater(
      http.get(Uri.parse('http://127.0.0.1:${server.port}/'), attempts: 3),
      throwsA(isA<MarketDataException>()),
    );
    expect(hits, 3);
  });

  test('403 с телом CloudFront доходит как блокировка страны', () async {
    // Тело выбрасывалось вместе с причиной: владелец видел «ответил 403» и
    // шёл проверять свои ключи, хотя ключи ни при чём.
    final server = await serve((request) {
      request.response.statusCode = 403;
      request.response.write(
        '<HTML><HEAD><title>ERROR: The request could not be satisfied</title>'
        '</HEAD><BODY>The Amazon CloudFront distribution is configured to '
        'block access from your country.</BODY></HTML>',
      );
      request.response.close();
    });
    addTearDown(() => server.close(force: true));

    final http = HttpJson(timeout: const Duration(seconds: 3));
    addTearDown(http.close);

    await expectLater(
      http.get(Uri.parse('http://127.0.0.1:${server.port}/'), attempts: 3),
      throwsA(
        isA<MarketDataException>()
            .having((e) => e.kind, 'kind', NetFailureKind.geoBlocked)
            .having((e) => e.isEnvironment, 'isEnvironment', isTrue)
            .having((e) => e.message, 'message', contains('страны')),
      ),
    );
  });
}
