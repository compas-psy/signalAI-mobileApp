import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';

class _FakeApi extends ApiClient {
  _FakeApi(this.response)
      : super(baseUrl: 'https://engine.test', deviceToken: '');

  final Map<String, dynamic> response;
  String? lastPath;

  @override
  Future<Map<String, dynamic>> get(String path) async {
    lastPath = path;
    return response;
  }
}

void main() {
  test('rebalance asks for the exact selected server portfolio model', () async {
    final api = _FakeApi({
      'model_id': 'model-selected',
      'needed': true,
      'urgent': false,
      'reason': 'расхождение выше порога',
      'max_drift': '0.30',
      'total_value': '100000',
      'actions': [
        {
          'instrument_id': 'MOEX:EQ:SBER',
          'symbol': 'SBER',
          'side': 'BUY',
          'target_weight': '0.50',
          'actual_weight': '0.20',
          'amount_rub': '30000',
          'reason': 'доля ниже цели',
        },
      ],
    });

    final result =
        await EngineClient(client: api).portfolioRebalance('model-selected');

    expect(api.lastPath, '/api/v1/portfolio/rebalance?model_id=model-selected');
    expect(result.isAvailable, isTrue);
    expect(result.modelId, 'model-selected');
    expect(result.needed, isTrue);
    expect(result.urgent, isFalse);
    expect(result.reason, 'расхождение выше порога');
    expect(result.maxDrift, closeTo(0.30, 1e-12));
    expect(result.totalValue, 100000);
    expect(result.actions.single.symbol, 'SBER');
    expect(result.actions.single.side, 'BUY');
    expect(result.actions.single.amountRub, 30000);
    expect(result.actions.single.actualWeight, closeTo(0.20, 1e-12));
    expect(result.actions.single.targetWeight, closeTo(0.50, 1e-12));
  });

  test('rebalance rejects a response for another portfolio model', () async {
    final api = _FakeApi({
      'model_id': 'another-model',
      'needed': true,
      'urgent': true,
      'reason': 'wrong package',
      'max_drift': '0.40',
      'total_value': '100000',
      'actions': const [],
    });

    final result =
        await EngineClient(client: api).portfolioRebalance('model-selected');

    expect(result.isAvailable, isFalse);
    expect(result.unavailableReason, contains('другого пакета'));
    expect(result.actions, isEmpty);
  });

  test('empty selected model fails closed without a network request', () async {
    final api = _FakeApi(const {});

    final result = await EngineClient(client: api).portfolioRebalance('  ');

    expect(api.lastPath, isNull);
    expect(result.isAvailable, isFalse);
    expect(result.unavailableReason, contains('Пакет не выбран'));
  });
}
