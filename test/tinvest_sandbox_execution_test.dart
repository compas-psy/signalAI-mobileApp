import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/tinvest_broker.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/broker/tinvest_role.dart';

class _MemoryCache implements TInvestInstrumentCache {
  final Map<String, TInvestInstrument> items = {};

  @override
  Future<TInvestInstrument?> get(String ticker) async => items[ticker];

  @override
  Future<void> put(String ticker, TInvestInstrument instrument) async {
    items[ticker] = instrument;
  }

  @override
  Future<String?> tickerFor(String? uid) async {
    if (uid == null) return null;
    for (final entry in items.entries) {
      if (entry.value.uid == uid) return entry.key;
    }
    return null;
  }
}

class _RequestLog {
  _RequestLog(this.path, this.body);
  final String path;
  final Map<String, dynamic> body;
}

bool _method(String path, String service, String method) =>
    path.endsWith('.$service/$method');

Future<({HttpServer server, List<_RequestLog> log})> _sandboxServer({
  bool existingSignalAiAccount = false,
}) async {
  final log = <_RequestLog>[];
  final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
  server.listen((request) async {
    final raw = await utf8.decoder.bind(request).join();
    final body = raw.isEmpty
        ? <String, dynamic>{}
        : jsonDecode(raw) as Map<String, dynamic>;
    log.add(_RequestLog(request.uri.path, body));

    Object answer;
    final path = request.uri.path;
    if (_method(path, 'SandboxService', 'GetSandboxAccounts')) {
      answer = {
        'accounts': existingSignalAiAccount
            ? [
                {
                  'id': 'sandbox-account',
                  'name': 'SignalAI risk sandbox',
                  'type': 'ACCOUNT_TYPE_TINKOFF',
                  'accessLevel': 'ACCOUNT_ACCESS_LEVEL_FULL_ACCESS',
                  'status': 'ACCOUNT_STATUS_OPEN',
                }
              ]
            : <Object>[],
      };
    } else if (_method(path, 'SandboxService', 'OpenSandboxAccount')) {
      answer = {'accountId': 'sandbox-account'};
    } else if (_method(path, 'SandboxService', 'SandboxPayIn')) {
      answer = {'balance': {'currency': 'rub', 'units': '300000', 'nano': 0}};
    } else if (_method(path, 'InstrumentsService', 'FutureBy')) {
      answer = {
        'instrument': {
          'uid': 'pxu6-uid',
          'ticker': 'PXU6',
          'lot': 1,
          'minPriceIncrement': {'units': '1', 'nano': 0},
        }
      };
    } else if (_method(path, 'SandboxService', 'PostSandboxOrder')) {
      answer = {
        'orderId': 'entry-order',
        'executionReportStatus': 'EXECUTION_REPORT_STATUS_NEW',
      };
    } else if (_method(path, 'SandboxService', 'PostSandboxStopOrder')) {
      answer = {'stopOrderId': 'stop-order'};
    } else if (_method(path, 'SandboxService', 'CancelSandboxOrder')) {
      answer = {'time': DateTime.now().toUtc().toIso8601String()};
    } else {
      request.response.statusCode = 404;
      answer = {'message': 'unexpected ${request.uri.path}'};
    }

    request.response.headers.contentType = ContentType.json;
    request.response.write(jsonEncode(answer));
    await request.response.close();
  });
  return (server: server, log: log);
}

void main() {
  test('new sandbox account is funded once with 300k risk capital', () async {
    final fixture = await _sandboxServer();
    addTearDown(() => fixture.server.close(force: true));
    final broker = TInvestBroker(
      mode: TradingMode.testnet,
      role: TInvestRole.sandbox,
      token: () async => 'sandbox-token',
      instrumentCache: _MemoryCache(),
      baseUrl: 'http://${fixture.server.address.host}:${fixture.server.port}',
    );
    addTearDown(broker.close);

    await broker.accounts();
    await broker.accounts();

    final open = fixture.log
        .where((r) => _method(r.path, 'SandboxService', 'OpenSandboxAccount'))
        .toList();
    final payIn = fixture.log
        .where((r) => _method(r.path, 'SandboxService', 'SandboxPayIn'))
        .toList();
    expect(open, hasLength(1));
    expect(open.single.body['name'], 'SignalAI risk sandbox');
    expect(payIn, hasLength(1));
    final amount = payIn.single.body['amount'] as Map<String, dynamic>;
    expect(amount['currency'], 'rub');
    expect(amount['units'], '300000');
  });

  test('existing SignalAI sandbox account is not topped up again', () async {
    final fixture = await _sandboxServer(existingSignalAiAccount: true);
    addTearDown(() => fixture.server.close(force: true));
    final broker = TInvestBroker(
      mode: TradingMode.testnet,
      role: TInvestRole.sandbox,
      token: () async => 'sandbox-token',
      instrumentCache: _MemoryCache(),
      baseUrl: 'http://${fixture.server.address.host}:${fixture.server.port}',
    );
    addTearDown(broker.close);

    await broker.accounts();

    expect(
      fixture.log.where((r) => _method(r.path, 'SandboxService', 'SandboxPayIn')),
      isEmpty,
    );
  });

  test('PXU6 sandbox entry and stop both use futures point pricing', () async {
    final fixture = await _sandboxServer(existingSignalAiAccount: true);
    addTearDown(() => fixture.server.close(force: true));
    final broker = TInvestBroker(
      mode: TradingMode.testnet,
      role: TInvestRole.sandbox,
      token: () async => 'sandbox-token',
      instrumentCache: _MemoryCache(),
      baseUrl: 'http://${fixture.server.address.host}:${fixture.server.port}',
    );
    addTearDown(broker.close);

    final result = await broker.placeOrder(const OrderRequest(
      symbol: 'PXU6',
      long: false,
      quantity: 10,
      entry: 13685,
      stopLoss: 13790,
      takeProfit: 13580,
    ));

    expect(result.accepted, isTrue);
    final entry = fixture.log.singleWhere(
      (r) => _method(r.path, 'SandboxService', 'PostSandboxOrder'),
    );
    expect(entry.body['instrumentId'], 'pxu6-uid');
    expect(entry.body['quantity'], '10');
    expect(entry.body['direction'], 'ORDER_DIRECTION_SELL');
    expect(entry.body['priceType'], 'PRICE_TYPE_POINT');
    expect(entry.body['timeInForce'], 'TIME_IN_FORCE_DAY');
    expect(entry.body['confirmMarginTrade'], isTrue);

    final stop = fixture.log.singleWhere(
      (r) => _method(r.path, 'SandboxService', 'PostSandboxStopOrder'),
    );
    expect(stop.body['instrumentId'], 'pxu6-uid');
    expect(stop.body['direction'], 'STOP_ORDER_DIRECTION_BUY');
    expect(stop.body['priceType'], 'PRICE_TYPE_POINT');
    expect(stop.body['confirmMarginTrade'], isTrue);
    expect((stop.body['orderId'] as String).length, lessThanOrEqualTo(36));

    expect(
      fixture.log.where((r) => r.path.contains('.StopOrdersService/')),
      isEmpty,
    );
  });
}
