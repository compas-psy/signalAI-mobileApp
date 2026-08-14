import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/tinvest_broker.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/broker/tinvest_role.dart';

class _FakeTInvestGateway {
  late final HttpServer _server;
  final List<({String method, Map<String, dynamic> body})> calls = [];

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((request) async {
      final raw = await utf8.decoder.bind(request).join();
      final method = request.uri.path.split('/').last;
      final body = raw.isEmpty
          ? <String, dynamic>{}
          : jsonDecode(raw) as Map<String, dynamic>;
      calls.add((method: method, body: body));

      final reply = switch (method) {
        'GetSandboxAccounts' => {
            'accounts': [
              {
                'id': 'ACC-1',
                'name': 'SignalAI risk sandbox',
                'status': 'ACCOUNT_STATUS_OPEN',
                'accessLevel': 'ACCOUNT_ACCESS_LEVEL_FULL_ACCESS',
              },
            ],
          },
        'FutureBy' => {
            'instrument': {
              'uid': 'UID-SI',
              'ticker': 'SiZ6',
              'lot': 1,
              'minPriceIncrement': {'units': '1', 'nano': 0},
            },
          },
        'PostSandboxOrder' => {
            'orderId': 'EXCHANGE-ENTRY-1',
            'orderRequestId': body['orderId'],
          },
        'PostSandboxStopOrder' => {
            'stopOrderId': 'EXCHANGE-STOP-1',
            'orderRequestId': body['orderId'],
          },
        _ => <String, dynamic>{},
      };

      request.response
        ..headers.contentType = ContentType.json
        ..write(jsonEncode(reply));
      await request.response.close();
    });
  }

  String get baseUrl => 'http://127.0.0.1:${_server.port}';

  Map<String, dynamic> bodyOf(String method) =>
      calls.firstWhere((call) => call.method == method).body;

  Future<void> close() => _server.close(force: true);
}

void main() {
  test('sandbox mirror reuses caller-owned provider ids for entry and stop', () async {
    final gateway = _FakeTInvestGateway();
    await gateway.start();
    addTearDown(gateway.close);

    final broker = TInvestBroker(
      mode: TradingMode.testnet,
      role: TInvestRole.sandbox,
      baseUrl: gateway.baseUrl,
      token: () async => 'T-SANDBOX',
      instrumentCache: StoredInstrumentCache(LocalStore.inMemory()),
    );
    addTearDown(broker.close);

    const entryRequestId = '11111111-1111-8111-8111-111111111111';
    const stopRequestId = '22222222-2222-8222-8222-222222222222';

    final result = await broker.placeOrder(
      const OrderRequest(
        symbol: 'SiZ6',
        long: true,
        quantity: 3,
        entry: 90000,
        stopLoss: 89500,
        takeProfit: 91000,
        requestId: entryRequestId,
        protectiveStopRequestId: stopRequestId,
      ),
    );

    expect(result.accepted, isTrue);
    expect(gateway.bodyOf('PostSandboxOrder')['orderId'], entryRequestId);
    expect(gateway.bodyOf('PostSandboxStopOrder')['orderId'], stopRequestId);
  });
}
