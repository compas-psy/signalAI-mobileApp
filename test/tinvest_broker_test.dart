import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/tinvest_broker.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/domain/broker/broker.dart';

/// Подставной шлюз брокера: отвечает по имени метода и запоминает вызовы.
class FakeGateway {
  FakeGateway(this.replies);

  /// Метод (последний сегмент пути) → ответ либо код ошибки.
  final Map<String, Object> replies;

  final List<({String method, Map<String, dynamic> body, String? auth})> calls = [];
  late final HttpServer server;

  Future<void> start() async {
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      final raw = await utf8.decoder.bind(request).join();
      final method = request.uri.path.split('/').last;
      calls.add((
        method: method,
        body: raw.isEmpty ? {} : jsonDecode(raw) as Map<String, dynamic>,
        auth: request.headers.value(HttpHeaders.authorizationHeader),
      ));

      final reply = replies[method];
      if (reply is int) {
        request.response.statusCode = reply;
        request.response.write(jsonEncode({'message': 'отказ метода $method'}));
      } else {
        request.response
          ..headers.contentType = ContentType.json
          ..write(jsonEncode(reply ?? const <String, dynamic>{}));
      }
      await request.response.close();
    });
  }

  String get baseUrl => 'http://127.0.0.1:${server.port}';

  List<String> get methods => [for (final c in calls) c.method];

  Map<String, dynamic> bodyOf(String method) =>
      calls.firstWhere((c) => c.method == method).body;
}

void main() {
  group('Формат цены', () {
    test('котировка брокера разбирается без потери копеек', () {
      expect(quotationToDouble({'units': '123', 'nano': 450000000}), closeTo(123.45, 1e-9));
      expect(quotationToDouble({'units': '0', 'nano': 10000000}), closeTo(0.01, 1e-9));
      expect(quotationToDouble({'units': '-5', 'nano': -500000000}), closeTo(-5.5, 1e-9));
      expect(quotationToDouble(null), 0);
    });

    test('число собирается обратно в котировку', () {
      expect(doubleToQuotation(123.45), {'units': '123', 'nano': 450000000});
      expect(doubleToQuotation(0.1), {'units': '0', 'nano': 100000000});
      expect(doubleToQuotation(90000), {'units': '90000', 'nano': 0});
    });

    test('отрицательная цена не ломает знак', () {
      final q = doubleToQuotation(-5.5);
      expect(q['units'], '-5');
      expect(q['nano'], -500000000);
    });

    test('преобразование туда-обратно совпадает', () {
      for (final value in [0.0, 1.0, 99.99, 90123.5, 0.0001]) {
        expect(quotationToDouble(doubleToQuotation(value)), closeTo(value, 1e-9));
      }
    });
  });

  group('TInvestBroker', () {
    late FakeGateway gateway;
    tearDown(() => gateway.server.close(force: true));

    /// Ответы, которых хватает на постановку заявки.
    Map<String, Object> okReplies() => {
          'GetSandboxAccounts': {
            'accounts': [
              {'id': 'ACC-1', 'status': 'ACCOUNT_STATUS_OPEN'},
            ],
          },
          'FutureBy': {
            'instrument': {
              'uid': 'UID-SI',
              'ticker': 'SiZ5',
              'lot': 1,
              'minPriceIncrement': {'units': '1', 'nano': 0},
            },
          },
          'PostSandboxOrder': {'orderId': 'ORD-1'},
          'PostStopOrder': {'stopOrderId': 'STOP-1'},
          'CancelSandboxOrder': <String, dynamic>{},
        };

    Future<TInvestBroker> broker({
      Map<String, Object>? replies,
      TradingMode mode = TradingMode.testnet,
      String? token = 'T-TOKEN',
    }) async {
      gateway = FakeGateway(replies ?? okReplies());
      await gateway.start();
      return TInvestBroker(
        mode: mode,
        baseUrl: gateway.baseUrl,
        token: () async => token,
        instrumentCache: StoredInstrumentCache(LocalStore.inMemory()),
      );
    }

    const request = OrderRequest(
      symbol: 'SiZ5',
      long: true,
      quantity: 3,
      entry: 90000,
      stopLoss: 89500,
      takeProfit: 91000,
    );

    test('заявка и стоп уходят вместе, объём в лотах', () async {
      final b = await broker();
      final result = await b.placeOrder(request);

      expect(result.accepted, isTrue);
      expect(result.orderId, 'ORD-1');
      expect(gateway.methods, contains('PostSandboxOrder'));
      expect(gateway.methods, contains('PostStopOrder'));

      final entry = gateway.bodyOf('PostSandboxOrder');
      expect(entry['accountId'], 'ACC-1');
      expect(entry['instrumentId'], 'UID-SI');
      expect(entry['direction'], 'ORDER_DIRECTION_BUY');
      expect(entry['quantity'], '3');
      expect(entry['price'], {'units': '90000', 'nano': 0});

      // Стоп идёт в обратную сторону — иначе он не защищает, а удваивает.
      final stop = gateway.bodyOf('PostStopOrder');
      expect(stop['direction'], 'STOP_ORDER_DIRECTION_SELL');
      expect(stop['stopPrice'], {'units': '89500', 'nano': 0});
    });

    test('токен уходит в заголовке авторизации', () async {
      final b = await broker();
      await b.checkAccess();
      expect(gateway.calls.first.auth, 'Bearer T-TOKEN');
    });

    test('отказ стопа снимает вход — позиция без защиты не остаётся', () async {
      final replies = okReplies()..['PostStopOrder'] = 400;
      final b = await broker(replies: replies);

      final result = await b.placeOrder(request);

      expect(result.accepted, isFalse);
      expect(result.message, contains('стоп'));
      expect(result.message, contains('снята'));
      expect(gateway.methods, contains('CancelSandboxOrder'),
          reason: 'вход обязан быть снят, иначе позиция окажется голой');
      expect(gateway.bodyOf('CancelSandboxOrder')['orderId'], 'ORD-1');
    });

    test('песочница и живой счёт зовут разные методы', () async {
      final live = await broker(
        mode: TradingMode.live,
        replies: {
          'GetAccounts': {
            'accounts': [
              {'id': 'ACC-LIVE', 'status': 'ACCOUNT_STATUS_OPEN'},
            ],
          },
        },
      );

      await live.checkAccess();
      expect(gateway.methods, contains('GetAccounts'));
      expect(gateway.methods, isNot(contains('GetSandboxAccounts')),
          reason: 'перепутать песочницу с живым счётом нельзя по построению');
    });

    test('без токена запрос не уходит', () async {
      final b = await broker(token: null);
      await expectLater(b.checkAccess(), throwsA(isA<BrokerException>()));
      expect(gateway.calls, isEmpty);
    });

    test('закрытые счета не выбираются для торговли', () async {
      final b = await broker(replies: {
        'GetSandboxAccounts': {
          'accounts': [
            {'id': 'ACC-OLD', 'status': 'ACCOUNT_STATUS_CLOSED'},
            {'id': 'ACC-NEW', 'status': 'ACCOUNT_STATUS_OPEN'},
          ],
        },
      });

      expect(await b.checkAccess(), contains('ACC-NEW'));
    });

    test('в песочнице без счетов он открывается сам', () async {
      final b = await broker(replies: {
        'GetSandboxAccounts': const <String, dynamic>{},
        'OpenSandboxAccount': {'accountId': 'ACC-FRESH'},
      });

      expect(await b.checkAccess(), contains('ACC-FRESH'));
      expect(gateway.methods, contains('OpenSandboxAccount'));
    });

    test('объём меньше лота заявкой не становится', () async {
      final replies = okReplies()
        ..['FutureBy'] = {
          'instrument': {
            'uid': 'UID-SI',
            'ticker': 'SiZ5',
            'lot': 10,
            'minPriceIncrement': {'units': '1', 'nano': 0},
          },
        };
      final b = await broker(replies: replies);

      final result = await b.placeOrder(request);
      expect(result.accepted, isFalse);
      expect(result.message, contains('лота'));
      expect(gateway.methods, isNot(contains('PostSandboxOrder')));
    });

    test('цена подгоняется под шаг контракта', () async {
      final replies = okReplies()
        ..['FutureBy'] = {
          'instrument': {
            'uid': 'UID-RI',
            'ticker': 'RIZ5',
            'lot': 1,
            'minPriceIncrement': {'units': '10', 'nano': 0},
          },
        };
      final b = await broker(replies: replies);

      await b.placeOrder(const OrderRequest(
        symbol: 'RIZ5',
        long: true,
        quantity: 1,
        entry: 110004,
        stopLoss: 109500,
        takeProfit: 111000,
      ));

      // 110004 не кратно десяти — брокер отклонил бы такую заявку.
      expect(gateway.bodyOf('PostSandboxOrder')['price'], {'units': '110000', 'nano': 0});
    });

    test('инструмент спрашивается у брокера один раз', () async {
      final b = await broker();
      await b.placeOrder(request);
      await b.placeOrder(request);

      expect(gateway.methods.where((m) => m == 'FutureBy'), hasLength(1),
          reason: 'лишний запрос в момент отправки — лишняя точка отказа');
    });

    test('неотозванный токен без прав объясняется словами', () async {
      final b = await broker(replies: {'GetSandboxAccounts': 403});

      await expectLater(
        b.checkAccess(),
        throwsA(isA<BrokerException>().having((e) => e.message, 'причина', contains('прав'))),
      );
    });

    /// Ответы с портфелем: одна длинная позиция по фьючерсу.
    Map<String, Object> portfolioReplies({List<Map<String, dynamic>>? stops}) => {
          'GetSandboxAccounts': {
            'accounts': [
              {'id': 'ACC-1', 'status': 'ACCOUNT_STATUS_OPEN'},
            ],
          },
          'GetSandboxPortfolio': {
            'positions': [
              {
                'instrumentType': 'currency',
                'instrumentUid': 'UID-RUB',
                'quantity': {'units': '100000', 'nano': 0},
              },
              {
                'instrumentType': 'futures',
                'instrumentUid': 'UID-SI',
                'figi': 'FUT-SI',
                'quantity': {'units': '2', 'nano': 0},
                'averagePositionPrice': {'units': '90000', 'nano': 0},
                'expectedYield': {'units': '350', 'nano': 500000000},
              },
            ],
          },
          'GetSandboxStopOrders': {'stopOrders': stops ?? const []},
          'FutureBy': {
            'instrument': {
              'uid': 'UID-SI',
              'ticker': 'SiZ5',
              'lot': 1,
              'minPriceIncrement': {'units': '1', 'nano': 0},
            },
          },
          'PostStopOrder': {'stopOrderId': 'STOP-9'},
        };

    test('позиция приходит с ценой входа и результатом', () async {
      final b = await broker(replies: portfolioReplies());

      final positions = await b.positions();
      expect(positions, hasLength(1), reason: 'валюта — не позиция срочного рынка');
      // Тикер, а не идентификатор: позицию мог открыть не мы, и тогда кэш о
      // ней ничего не знает — брокера надо спросить, а не показать GUID.
      expect(positions.single.symbol, 'SiZ5');
      expect(positions.single.quantity, 2);
      expect(positions.single.entryPrice, 90000);
      expect(positions.single.unrealizedPnl, closeTo(350.5, 1e-9));
    });

    test('позиция без стоп-заявки считается голой', () async {
      final b = await broker(replies: portfolioReplies());

      final naked = await b.unprotectedPositions();
      expect(naked, hasLength(1));
      expect(naked.single.unprotected, isTrue);
    });

    test('позиция со стоп-заявкой голой не считается', () async {
      final b = await broker(replies: portfolioReplies(stops: [
        {'instrumentUid': 'UID-SI', 'stopOrderId': 'S-1'},
      ]));

      expect(await b.unprotectedPositions(), isEmpty);
    });

    test('защита от стоп-заявки не выдаётся за уровень стопа', () async {
      final b = await broker(replies: portfolioReplies(stops: [
        {'instrumentUid': 'UID-SI', 'stopOrderId': 'S-1'},
      ]));

      final position = (await b.positions()).single;
      expect(position.protectedByStop, isTrue);
      // Цена стопа брокером не отдаётся. Раньше вместо неё стояла единица —
      // и на экране это читалось как «стоп на уровне 1» по контракту за 90 000.
      expect(position.stopLoss, 0);
    });

    test('недоступный список стопов не выдаёт позицию за защищённую', () async {
      final replies = portfolioReplies()..['GetSandboxStopOrders'] = 500;
      final b = await broker(replies: replies);

      // Молчаливое «наверное, стоп есть» — худший из возможных ответов.
      expect(await b.unprotectedPositions(), hasLength(1));
    });

    test('защитный стоп ставится в обратную сторону', () async {
      final b = await broker(replies: portfolioReplies());

      final ok = await b.placeProtectiveStop(
        symbol: 'SiZ5',
        stopPrice: 89500,
        long: true,
        quantity: 2,
      );

      expect(ok, isTrue);
      final body = gateway.bodyOf('PostStopOrder');
      expect(body['direction'], 'STOP_ORDER_DIRECTION_SELL');
      expect(body['quantity'], '2');
      expect(body['stopPrice'], {'units': '89500', 'nano': 0});
    });

    test('отказ брокера в стопе возвращается как false, а не исключением', () async {
      final replies = portfolioReplies()..['PostStopOrder'] = 400;
      final b = await broker(replies: replies);

      expect(
        await b.placeProtectiveStop(
          symbol: 'SiZ5',
          stopPrice: 89500,
          long: true,
          quantity: 2,
        ),
        isFalse,
      );
    });
  });
}
