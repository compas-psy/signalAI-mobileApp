import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/signal.dart';

TradingSignal _signal({
  required Direction direction,
  required double entry,
  required double stop,
  required List<double> targets,
}) {
  return TradingSignal(
    id: 'geometry-fixture',
    symbol: 'LINKUSDT',
    name: 'Chainlink',
    market: Market.crypto,
    direction: direction,
    horizon: Horizon.swing,
    horizonLabel: '1–3 дня',
    score: 60,
    entry: entry,
    stopLoss: stop,
    takeProfits: [
      for (var i = 0; i < targets.length; i++)
        TakeProfit(index: i + 1, price: targets[i], sharePercent: 33),
    ],
    priceDecimals: 3,
    riskReward: '0.3',
    chips: const [],
    note: '',
    factors: const [],
    events: const [],
    unitRisk: 1,
    unitRiskLabel: '',
    unitMultiplier: 1,
    unitDecimals: 3,
    unitName: 'LINK',
    lastPrice: '90.525',
    changeLabel: '',
    changeUp: true,
    status: SignalStatus.proposed,
  );
}

void main() {
  group('TradingSignal trade-plan geometry', () {
    test('rejects the LINKUSDT regression where TP1 is below LONG entry', () {
      final signal = _signal(
        direction: Direction.long,
        entry: 90.525,
        stop: 89.431,
        targets: const [90.457, 90.813],
      );

      expect(
        signal.tradePlanBlockers(minRiskRewardToTp2: 1.5),
        isNotEmpty,
      );
    });

    test('accepts ordered LONG levels with sufficient TP2 reward', () {
      final signal = _signal(
        direction: Direction.long,
        entry: 100,
        stop: 99,
        targets: const [101.2, 102.0, 103.0],
      );

      expect(
        signal.tradePlanBlockers(minRiskRewardToTp2: 1.5),
        isEmpty,
      );
    });

    test('accepts ordered SHORT levels with sufficient TP2 reward', () {
      final signal = _signal(
        direction: Direction.short,
        entry: 100,
        stop: 101,
        targets: const [98.8, 98.0, 97.0],
      );

      expect(
        signal.tradePlanBlockers(minRiskRewardToTp2: 1.5),
        isEmpty,
      );
    });
  });
}
