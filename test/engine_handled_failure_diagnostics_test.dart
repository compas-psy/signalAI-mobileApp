import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';

class _Api extends ApiClient {
  _Api({
    this.detailError,
    this.barsByTimeframe = const <String, List<dynamic>>{},
  }) : super(baseUrl: 'https://engine.test', deviceToken: '');

  final Object? detailError;
  final Map<String, List<dynamic>> barsByTimeframe;

  @override
  Future<Map<String, dynamic>> get(String path) async {
    final error = detailError;
    if (error != null) throw error;
    return {
      'id': 'idea-1',
      'instrument_id': 'CRYPTO:PERP:BTCUSDT',
      'symbol': 'BTCUSDT',
      'strategy': 'TREND_PULLBACK',
      'direction': 'LONG',
      'status': 'TRIGGERED',
      'quality_status': 'ACTIVE',
      'score': '82',
      'signal_time': '2026-08-06T09:00:00Z',
      'expires_at': '2026-08-07T09:00:00Z',
    };
  }

  @override
  Future<List<dynamic>> getList(String path) async {
    final uri = Uri.parse('https://engine.test$path');
    return barsByTimeframe[uri.queryParameters['timeframe']] ?? const [];
  }
}

void main() {
  test('detail hydration failure stays non-fatal but is reported once', () async {
    final reports = <EngineHandledFailure>[];
    final client = EngineClient(
      client: _Api(
        detailError: ApiException(
          'Authorization: Bearer secret-detail-token',
        ),
      ),
      onHandledFailure: reports.add,
    );

    final detail = await client.detail('idea-1');

    expect(detail, isNull);
    expect(reports, hasLength(1));
    expect(reports.single.stage, EngineFailureStage.ideaHydration);
    expect(reports.single.error.toString(), contains('secret-detail-token'));
    expect(reports.single.stackTrace, isNotNull);
  });

  test('chart fallback exhaustion reports one final chart-load failure', () async {
    final reports = <EngineHandledFailure>[];
    final userFailures = <String>[];
    final client = EngineClient(
      client: _Api(),
      onHandledFailure: reports.add,
    );

    final chart = await client.barsWithFallback(
      'MOEX:FUT:SIU6',
      setupTimeframe: '15m',
      onFailure: userFailures.add,
    );

    expect(chart, isNull);
    expect(userFailures, hasLength(1));
    expect(reports, hasLength(1));
    expect(reports.single.stage, EngineFailureStage.chartLoad);
    expect(reports.single.error.toString(), contains('15m, 4h, 1h, 1d'));
    expect(reports.single.error.toString(), isNot(contains('MOEX:FUT:SIU6')));
  });

  test('successful detail and chart do not emit handled failures', () async {
    final reports = <EngineHandledFailure>[];
    final api = _Api(
      barsByTimeframe: {
        '4h': [
          {
            'open_time': '2026-08-06T08:00:00Z',
            'open': '100',
            'high': '102',
            'low': '99',
            'close': '101',
          },
        ],
      },
    );
    final client = EngineClient(client: api, onHandledFailure: reports.add);

    expect(await client.detail('idea-1'), isNotNull);
    expect(
      await client.barsWithFallback(
        'CRYPTO:PERP:BTCUSDT',
        setupTimeframe: 'H4',
      ),
      isNotNull,
    );
    expect(reports, isEmpty);
  });
}
