import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/tinvest_sandbox_mirror_reconciler.dart';

class _FakeGateway {
  late final HttpServer _server;
  int entryStateStatus = 200;
  Map<String, dynamic> entryState = {
    'orderId': 'EXCHANGE-1',
    'orderRequestId': 'e-11111111222243338444555555555555',
    'instrumentUid': 'UID-SI',
    'ticker': 'SiZ6',
    'lotsRequested': '3',
    'executionReportStatus': 'EXECUTION_REPORT_STATUS_NEW',
  };
  List<Map<String, dynamic>> stopOrders = [];
  final List<({String method, Map<String, dynamic> body})> calls = [];

  Future<void> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((request) async {
      final raw = await utf8.decoder.bind(request).join();
      final body = raw.isEmpty
          ? <String, dynamic>{}
          : jsonDecode(raw) as Map<String, dynamic>;
      final method = request.uri.path.split('/').last;
      calls.add((method: method, body: body));

      if (method == 'GetSandboxOrderState') {
        request.response.statusCode = entryStateStatus;
        if (entryStateStatus == 200) request.response.write(jsonEncode(entryState));
      } else if (method == 'GetSandboxStopOrders') {
        request.response.write(jsonEncode({'stopOrders': stopOrders}));
      } else if (method == 'PostSandboxStopOrder') {
        request.response.write(jsonEncode({
          'stopOrderId': 'STOP-1',
          'orderRequestId': body['orderId'],
        }));
      } else {
        request.response.statusCode = 404;
      }
      await request.response.close();
    });
  }

  String get baseUrl => 'http://127.0.0.1:${_server.port}';

  Future<void> close() => _server.close(force: true);
}

void main() {
  const entryId = 'e-11111111222243338444555555555555';
  const stopId = 's-11111111222243338444555555555555';

  late _FakeGateway gateway;
  late TInvestSandboxMirrorReconciler reconciler;

  setUp(() async {
    gateway = _FakeGateway();
    await gateway.start();
    reconciler = TInvestSandboxMirrorReconciler(
      token: 'sandbox-token',
      baseUrl: gateway.baseUrl,
    );
  });

  tearDown(() async {
    reconciler.close();
    await gateway.close();
  });

  test('404 by request id means the entry was not delivered', () async {
    gateway.entryStateStatus = 404;

    final result = await reconciler.probe(
      accountId: 'ACC-1',
      entryRequestId: entryId,
      symbol: 'SiZ6',
      long: true,
      stopPrice: 89500,
    );

    expect(result.status, TInvestSandboxMirrorProbeStatus.absent);
    expect(gateway.calls.first.body['orderIdType'], 'ORDER_ID_TYPE_REQUEST');
    expect(gateway.calls.first.body['orderId'], entryId);
  });

  test('entry plus matching protective stop is already complete', () async {
    gateway.stopOrders = [
      {
        'stopOrderId': 'STOP-1',
        'instrumentUid': 'UID-SI',
        'ticker': 'SiZ6',
        'lotsRequested': '3',
        'direction': 'STOP_ORDER_DIRECTION_SELL',
        'stopPrice': {'units': '89500', 'nano': 0},
        'status': 'STOP_ORDER_STATUS_ACTIVE',
      },
    ];

    final result = await reconciler.probe(
      accountId: 'ACC-1',
      entryRequestId: entryId,
      symbol: 'SiZ6',
      long: true,
      stopPrice: 89500,
    );

    expect(result.status, TInvestSandboxMirrorProbeStatus.protected);
    expect(result.exchangeOrderId, 'EXCHANGE-1');
    expect(result.instrumentUid, 'UID-SI');
    expect(result.lotsRequested, 3);
  });

  test('entry without matching stop can repair only the protection', () async {
    final result = await reconciler.probe(
      accountId: 'ACC-1',
      entryRequestId: entryId,
      symbol: 'SiZ6',
      long: true,
      stopPrice: 89500,
    );
    expect(result.status, TInvestSandboxMirrorProbeStatus.entryWithoutProtection);

    final repaired = await reconciler.ensureProtectiveStop(
      accountId: 'ACC-1',
      instrumentUid: result.instrumentUid,
      lots: result.lotsRequested,
      long: true,
      stopPrice: 89500,
      requestId: stopId,
    );

    expect(repaired, isTrue);
    final post = gateway.calls.singleWhere(
      (call) => call.method == 'PostSandboxStopOrder',
    );
    expect(post.body['orderId'], stopId);
    expect(post.body['instrumentId'], 'UID-SI');
    expect(post.body['quantity'], '3');
  });

  test('provider transport ambiguity returns unavailable and does not post', () async {
    gateway.entryStateStatus = 503;

    final result = await reconciler.probe(
      accountId: 'ACC-1',
      entryRequestId: entryId,
      symbol: 'SiZ6',
      long: true,
      stopPrice: 89500,
    );

    expect(result.status, TInvestSandboxMirrorProbeStatus.unavailable);
    expect(
      gateway.calls.where((call) => call.method.startsWith('PostSandbox')),
      isEmpty,
    );
  });

  test('cancelled entry becomes explicit repair state, never a new post', () async {
    gateway.entryState = {
      ...gateway.entryState,
      'executionReportStatus': 'EXECUTION_REPORT_STATUS_CANCELLED',
    };

    final result = await reconciler.probe(
      accountId: 'ACC-1',
      entryRequestId: entryId,
      symbol: 'SiZ6',
      long: true,
      stopPrice: 89500,
    );

    expect(result.status, TInvestSandboxMirrorProbeStatus.ambiguous);
    expect(result.message, contains('CANCELLED'));
    expect(
      gateway.calls.where((call) => call.method.startsWith('PostSandbox')),
      isEmpty,
    );
  });

  test('cancelled matching stop is not mistaken for active protection', () async {
    gateway.stopOrders = [
      {
        'stopOrderId': 'STOP-OLD',
        'instrumentUid': 'UID-SI',
        'ticker': 'SiZ6',
        'lotsRequested': '3',
        'direction': 'STOP_ORDER_DIRECTION_SELL',
        'stopPrice': {'units': '89500', 'nano': 0},
        'status': 'STOP_ORDER_STATUS_CANCELED',
      },
    ];

    final result = await reconciler.probe(
      accountId: 'ACC-1',
      entryRequestId: entryId,
      symbol: 'SiZ6',
      long: true,
      stopPrice: 89500,
    );

    expect(result.status, TInvestSandboxMirrorProbeStatus.ambiguous);
    expect(result.message, contains('снят'));
  });
}
