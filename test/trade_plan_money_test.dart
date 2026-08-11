import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/trade_plan.dart';
import 'package:signalai/domain/idea/trade_plan_money.dart';

void main() {
  test('PXU6 105 points equals 105 RUB per contract when MOEX point value is 1', () {
    final plan = TradePlan(
      instrumentId: 'MOEX:FUT:PXU6',
      direction: Direction.short,
      orderType: PlanOrderType.limit,
      entryLow: 13675,
      entryHigh: 13695,
      entryReference: 13685,
      maxSlippagePercent: 0.1,
      stop: 13790,
      targets: const [
        PlanTarget(name: 'TP1', price: 13580, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP2', price: 13475, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP3', price: 13370, fraction: 0.2, afterFill: ''),
      ],
      quantity: 14,
      quantityStep: 1,
      quantityUnit: 'контр.',
      lotSize: 1,
      // MOEX MINSTEP=1, STEPPRICE=1 -> one price point = 1 RUB/contract.
      valuePerPoint: 1,
      quoteCurrency: 'RUB',
      riskRubles: 1470,
      riskPercent: 0.5,
      marginEstimate: 0,
      expiry: DateTime.utc(2026, 8, 16),
      strategyVersion: '0.1.0',
    );

    expect(plan.pnlRublesAt(13580), closeTo(1470, 1e-9));
    expect(plan.pnlRublesAt(13475), closeTo(2940, 1e-9));
    expect(plan.pnlRublesAt(13370), closeTo(4410, 1e-9));
    expect(plan.potentialProfitRubles, closeTo(2646, 1e-9));
    expect(plan.potentialLossRubles, closeTo(1470, 1e-9));
  });

  test('foreign quote converts point PnL using server quote rate', () {
    final plan = TradePlan(
      instrumentId: 'CRYPTO:PERP:BTCUSDT',
      direction: Direction.long,
      orderType: PlanOrderType.limit,
      entryLow: 100,
      entryHigh: 100,
      entryReference: 100,
      maxSlippagePercent: 0.1,
      stop: 90,
      targets: const [
        PlanTarget(name: 'TP1', price: 110, fraction: 1, afterFill: ''),
      ],
      quantity: 2,
      quantityStep: 0.001,
      quantityUnit: 'BTC',
      lotSize: 1,
      valuePerPoint: 1,
      quoteCurrency: 'USDT',
      quoteRateRub: 80,
      riskRubles: 1600,
      riskPercent: 0.5,
      marginEstimate: 0,
      expiry: DateTime.utc(2026, 8, 16),
      strategyVersion: '0.1.0',
    );

    expect(plan.pnlRublesAt(110), closeTo(1600, 1e-9));
  });
}
