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
      'actionable': false,
      'economics_status': 'UNKNOWN',
      'estimated_costs_rub': null,
      'estimated_tax_rub': null,
      'actions': [
        {
          'instrument_id': 'MOEX:EQ:SBER',
          'symbol': 'SBER',
          'side': 'BUY',
          'target_weight': '0.50',
          'actual_weight': '0.20',
          'amount_rub': '30000',
          'reason': 'доля ниже цели',
          'economics_status': 'UNKNOWN',
          'actionable': false,
          'order_quantity': null,
          'order_notional_rub': null,
          'estimated_costs_rub': null,
          'estimated_tax_rub': null,
          'broker_final_costs_rub': null,
          'broker_final_tax_rub': null,
          'economics_provenance': {'policy_id': 'missing'},
          'economics_blockers': ['fee_policy', 'cost_basis'],
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
    expect(result.actionable, isFalse);
    expect(result.economicsStatus, 'UNKNOWN');
    expect(result.estimatedCostsRub, isNull);
    expect(result.estimatedTaxRub, isNull);
    expect(result.actions.single.symbol, 'SBER');
    expect(result.actions.single.side, 'BUY');
    expect(result.actions.single.amountRub, 30000);
    expect(result.actions.single.actualWeight, closeTo(0.20, 1e-12));
    expect(result.actions.single.targetWeight, closeTo(0.50, 1e-12));
    expect(result.actions.single.actionable, isFalse);
    expect(result.actions.single.economicsStatus, 'UNKNOWN');
    expect(result.actions.single.orderQuantity, isNull);
    expect(result.actions.single.orderNotionalRub, isNull);
    expect(result.actions.single.estimatedCostsRub, isNull);
    expect(result.actions.single.estimatedTaxRub, isNull);
    expect(result.actions.single.economicsProvenance['policy_id'], 'missing');
    expect(result.actions.single.economicsBlockers, ['fee_policy', 'cost_basis']);
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

  test('rebalance retains broker final action economics and provenance', () async {
    final api = _FakeApi({
      'model_id': 'model-selected',
      'needed': true,
      'actionable': true,
      'economics_status': 'BROKER_FINAL',
      'estimated_costs_rub': '45.00',
      'estimated_tax_rub': '1500.00',
      'broker_final_costs_rub': '46.00',
      'broker_final_tax_rub': '1501.00',
      'actions': [
        {
          'instrument_id': 'MOEX:EQ:SBER',
          'symbol': 'SBER',
          'side': 'SELL',
          'target_weight': '0.50',
          'actual_weight': '0.80',
          'amount_rub': '30000',
          'reason': 'доля выше цели',
          'economics_status': 'BROKER_FINAL',
          'actionable': true,
          'order_quantity': '300',
          'order_notional_rub': '30000.00',
          'estimated_costs_rub': '45.00',
          'estimated_tax_rub': '1500.00',
          'broker_final_costs_rub': '46.00',
          'broker_final_tax_rub': '1501.00',
          'economics_provenance': {
            'broker_final_reference': 'broker:A:2026-08-22',
          },
          'economics_blockers': const [],
        },
      ],
    });

    final result =
        await EngineClient(client: api).portfolioRebalance('model-selected');

    final action = result.actions.single;
    expect(result.economicsStatus, 'BROKER_FINAL');
    expect(result.brokerFinalCostsRub, 46);
    expect(result.brokerFinalTaxRub, 1501);
    expect(action.actionable, isTrue);
    expect(action.orderQuantity, 300);
    expect(action.orderNotionalRub, 30000);
    expect(action.estimatedCostsRub, 45);
    expect(action.estimatedTaxRub, 1500);
    expect(action.brokerFinalCostsRub, 46);
    expect(action.brokerFinalTaxRub, 1501);
    expect(action.economicsProvenance['broker_final_reference'],
        'broker:A:2026-08-22');
  });

  test('empty selected model fails closed without a network request', () async {
    final api = _FakeApi(const {});

    final result = await EngineClient(client: api).portfolioRebalance('  ');

    expect(api.lastPath, isNull);
    expect(result.isAvailable, isFalse);
    expect(result.unavailableReason, contains('Пакет не выбран'));
  });
}
