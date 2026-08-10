import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/core/format.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/trade_plan.dart';

void main() {
  test('DOGE plan displays executable reference, not lower zone boundary', () {
    final plan = TradePlan(
      instrumentId: 'CRYPTO:PERP:DOGEUSDT',
      direction: Direction.short,
      orderType: PlanOrderType.limit,
      entryLow: 0.06940,
      entryHigh: 0.07033,
      maxSlippagePercent: 0.1,
      stop: 0.07059,
      targets: const [
        PlanTarget(name: 'TP1', price: 0.06913, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP2', price: 0.06840, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP3', price: 0.06767, fraction: 0.2, afterFill: ''),
      ],
      quantity: 1,
      lotSize: 1,
      valuePerPoint: 1,
      riskRubles: 100,
      riskPercent: 0.5,
      marginEstimate: 0,
      expiry: DateTime.utc(2026, 8, 14),
      strategyVersion: '0.1.0',
    );

    expect(fmtPrice(plan.entry, 5), '0,06987');
    expect(fmtPrice(plan.entryLow, 5), '0,06940');
    expect(plan.entry, isNot(plan.entryLow));
  });
}
