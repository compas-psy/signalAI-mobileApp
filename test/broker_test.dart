import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/bybit_broker.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/broker/trading_gate.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';

/// Перехваченный запрос к «бирже».
class Captured {
  Captured(this.method, this.path, this.headers, this.body);

  final String method;
  final String path;
  final Map<String, String> headers;
  final String body;
}

/// Локальная подставная биржа: отвечает заготовкой и запоминает запрос.
Future<(HttpServer, List<Captured>)> fakeExchange(Map<String, dynamic> reply) async {
  final captured = <Captured>[];
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((request) async {
    final body = await utf8.decoder.bind(request).join();
    final headers = <String, String>{};
    request.headers.forEach((name, values) => headers[name] = values.join(','));
    captured.add(Captured(request.method, request.uri.path, headers, body));
    request.response
      ..headers.contentType = ContentType.json
      ..write(jsonEncode(reply));
    await request.response.close();
  });
  return (server, captured);
}

void main() {
  group('BybitBroker', () {
    late HttpServer server;
    late List<Captured> captured;
    final signed = <String>[];

    setUp(() => signed.clear());
    tearDown(() => server.close(force: true));

    Future<BybitBroker> broker({
      required Map<String, dynamic> reply,
      String? apiKey = 'KEY123',
      String? signature = 'deadbeef',
    }) async {
      final (s, c) = await fakeExchange(reply);
      server = s;
      captured = c;
      return BybitBroker(
        mode: TradingMode.testnet,
        baseUrl: 'http://127.0.0.1:${server.port}',
        apiKey: () async => apiKey,
        signer: (payload) async {
          signed.add(payload);
          return signature;
        },
      );
    }

    test('ордер подписан той же строкой, что уходит в теле', () async {
      final b = await broker(reply: {
        'retCode': 0,
        'result': {'orderId': 'ORD-1'},
      });

      final result = await b.placeOrder(const OrderRequest(
        symbol: 'BTCUSDT',
        long: true,
        quantity: 0.01,
        entry: 60000,
        stopLoss: 58000,
        takeProfit: 63000,
      ));

      expect(result.accepted, isTrue);
      expect(result.orderId, 'ORD-1');

      final request = captured.single;
      expect(request.method, 'POST');
      expect(request.path, '/v5/order/create');
      // Ключевой инвариант: подпись считается по той же строке, что уходит на
      // биржу. Пересобери JSON перед отправкой — и подпись перестанет сходиться.
      expect(signed.single, endsWith(request.body));
      expect(signed.single, contains('KEY123'));
      expect(request.headers['x-bapi-sign'], 'deadbeef');
      expect(request.headers['x-bapi-api-key'], 'KEY123');
      expect(request.headers['x-bapi-recv-window'], '5000');
    });

    test('стоп и цель уходят вместе с ордером', () async {
      final b = await broker(reply: {
        'retCode': 0,
        'result': {'orderId': 'ORD-2'},
      });

      await b.placeOrder(const OrderRequest(
        symbol: 'BTCUSDT',
        long: false,
        quantity: 0.5,
        entry: 100,
        stopLoss: 110,
        takeProfit: 80,
      ));

      final body = jsonDecode(captured.single.body) as Map<String, dynamic>;
      expect(body['side'], 'Sell');
      expect(body['stopLoss'], '110');
      expect(body['takeProfit'], '80');
      expect(body['qty'], '0.5', reason: 'дробный объём не должен уйти экспонентой');
    });

    test('отказ биржи возвращается человеческой причиной, а не исключением', () async {
      final b = await broker(reply: {'retCode': 110007, 'retMsg': 'insufficient balance'});

      final result = await b.placeOrder(const OrderRequest(
        symbol: 'BTCUSDT',
        long: true,
        quantity: 1,
        entry: 60000,
        stopLoss: 58000,
        takeProfit: 63000,
      ));

      expect(result.accepted, isFalse);
      expect(result.message, contains('insufficient balance'));
    });

    test('без ключа запрос на биржу не уходит', () async {
      final b = await broker(reply: const {'retCode': 0}, apiKey: null);

      await expectLater(b.checkAccess(), throwsA(isA<BrokerException>()));
      expect(captured, isEmpty);
      expect(signed, isEmpty);
    });

    test('без подписи запрос на биржу не уходит', () async {
      final b = await broker(reply: const {'retCode': 0}, signature: null);

      await expectLater(b.checkAccess(), throwsA(isA<BrokerException>()));
      expect(captured, isEmpty, reason: 'неподписанный запрос отправлять нельзя');
    });

    test('нулевые позиции не показываются как открытые', () async {
      final b = await broker(reply: {
        'retCode': 0,
        'result': {
          'list': [
            {'symbol': 'BTCUSDT', 'side': 'Buy', 'size': '0', 'avgPrice': '0'},
            {
              'symbol': 'ETHUSDT',
              'side': 'Sell',
              'size': '1.5',
              'avgPrice': '3000',
              'unrealisedPnl': '-12.5',
            },
          ],
        },
      });

      final positions = await b.positions();
      expect(positions, hasLength(1));
      expect(positions.single.symbol, 'ETHUSDT');
      expect(positions.single.long, isFalse);
      expect(positions.single.unrealizedPnl, -12.5);
    });

    test('testnet и live — разные адреса', () {
      final testnet = BybitBroker(
        mode: TradingMode.testnet,
        apiKey: () async => 'k',
        signer: (_) async => 's',
      );
      final live = BybitBroker(
        mode: TradingMode.live,
        apiKey: () async => 'k',
        signer: (_) async => 's',
      );
      expect(testnet.mode, TradingMode.testnet);
      expect(live.mode, TradingMode.live);
      addTearDown(testnet.close);
      addTearDown(live.close);
    });
  });

  group('LiveTradingGate', () {
    PaperTrade closed(double resultR, int i) => PaperTrade(
          id: 'T$i',
          symbol: 'BTCUSDT',
          strategyId: 'crypto',
          long: true,
          entry: 100,
          stopLoss: 90,
          tpPrices: const [110],
          tpShares: const [100],
          score: 70,
          createdAt: DateTime.utc(2026, 3, i + 1),
          status: PaperStatus.closed,
          resultR: resultR,
          closedAt: DateTime.utc(2026, 3, i + 1, 20),
        );

    test('малая выборка живой счёт не открывает', () {
      final ledger = SignalLedger(trades: [for (var i = 0; i < 5; i++) closed(2, i)]);
      final verdict = const LiveTradingGate().evaluate(ledger);

      expect(verdict.allowed, isFalse);
      expect(verdict.reason, contains('20 закрытых'));
      expect(verdict.progress, closeTo(0.25, 1e-9));
    });

    test('убыточная стратегия живой счёт не открывает', () {
      final ledger = SignalLedger(trades: [
        for (var i = 0; i < 15; i++) closed(-1, i),
        for (var i = 15; i < 25; i++) closed(1, i),
      ]);
      final verdict = const LiveTradingGate().evaluate(ledger);

      expect(verdict.allowed, isFalse);
      expect(verdict.reason, contains('профит-фактор'));
    });

    test('выборка набрана и прибыльна — допуск открыт', () {
      final ledger = SignalLedger(trades: [
        for (var i = 0; i < 10; i++) closed(-1, i),
        for (var i = 10; i < 25; i++) closed(2, i),
      ]);
      final verdict = const LiveTradingGate().evaluate(ledger);

      expect(verdict.allowed, isTrue);
      expect(verdict.reason, contains('PF'));
    });

    test('пустой журнал — запрет, а не разрешение по умолчанию', () {
      expect(const LiveTradingGate().evaluate(SignalLedger()).allowed, isFalse);
    });
  });

  group('TradingState', () {
    test('аварийная остановка запрещает отправку ордеров', () {
      const state = TradingState(enabled: true, killSwitch: true);
      expect(state.canSendOrders, isFalse);
    });

    test('выключенная торговля ордера не шлёт', () {
      expect(const TradingState().canSendOrders, isFalse);
    });

    test('включённая торговля без аварийной остановки разрешена', () {
      expect(const TradingState(enabled: true).canSendOrders, isTrue);
    });

    test('состояние переживает сериализацию', () {
      const state = TradingState(mode: TradingMode.live, enabled: true, killSwitch: true);
      final restored = TradingState.fromJson(state.toJson());
      expect(restored.mode, TradingMode.live);
      expect(restored.enabled, isTrue);
      expect(restored.killSwitch, isTrue);
    });

    test('по умолчанию режим тренировочный, торговля выключена', () {
      const state = TradingState();
      expect(state.mode, TradingMode.testnet);
      expect(state.enabled, isFalse);
    });
  });
}
