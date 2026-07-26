import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/backtester.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/factor_audit.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/optimizer.dart';
import 'package:signalai/domain/analysis/screener.dart';
import 'package:signalai/domain/analysis/trade_simulator.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/signal.dart';

/// Скринер-заглушка: либо стреляет на каждом опросе (движок сам ограничит
/// частоту сделок кулдауном), либо молчит. Так проверяется логика выбора
/// оптимизатора, а не качество самого скринера — оно покрыто своими тестами.
class StubScreener extends Screener {
  const StubScreener({required this.fires});

  final bool fires;

  @override
  ScreenerResult? evaluate(
    InstrumentInput input,
    MarketRegime regime, {
    DateTime? now,
    List<RejectedCandidate>? rejected,
    List<FactorEdge>? factorHistory,
  }) {
    if (!fires) return null;
    final entry = input.lastPrice;
    const risk = 2.0;
    return ScreenerResult(
      components: const [],
      signal: TradingSignal(
        id: 'stub',
        symbol: input.spec.symbol,
        name: input.spec.name,
        market: input.spec.market,
        direction: Direction.long,
        horizon: Horizon.swing,
        horizonLabel: '',
        score: 80,
        entry: entry,
        stopLoss: entry - risk,
        takeProfits: [
          for (var i = 0; i < 3; i++)
            TakeProfit(
              index: i + 1,
              price: entry + risk * Screener.defaultTakeProfitMultiples[i],
              sharePercent: Screener.defaultTakeProfitShares[i],
            ),
        ],
        priceDecimals: 0,
        riskReward: '2,2',
        chips: const [],
        note: '',
        factors: const [],
        events: const [],
        unitRisk: risk,
        unitRiskLabel: '',
        unitMultiplier: 1,
        unitDecimals: 0,
        unitName: 'конт.',
        lastPrice: '$entry',
        changeLabel: '',
        changeUp: true,
        status: SignalStatus.proposed,
      ),
    );
  }
}

const spec = InstrumentSpec(
  id: 'test',
  symbol: 'TSTU6',
  name: 'Тест',
  market: Market.forts,
  priceDecimals: 0,
  valuePerPoint: 1,
  unitMultiplier: 1,
  unitDecimals: 0,
  unitName: 'конт.',
  unitRiskSuffix: 'контракт',
  tickSize: 0.01,
);

final start = DateTime.utc(2026, 2, 2, 10);

/// Плавно растущий часовик: любой лонг в нём в итоге прибыльный.
InstrumentHistory risingHistory() {
  final hourly = <Candle>[];
  var value = 100.0;
  for (var i = 0; i < 520; i++) {
    final next = i < 120 ? value : value + 0.5;
    hourly.add(Candle(
      time: start.add(Duration(hours: i)),
      open: value,
      high: (next > value ? next : value) + 0.05,
      low: (next < value ? next : value) - 0.05,
      close: next,
      volume: 100,
    ));
    value = next;
  }
  final daily = [
    for (var i = 60; i > 0; i--)
      Candle(
        time: start.subtract(Duration(days: i)),
        open: 100,
        high: 101,
        low: 99,
        close: 100,
        volume: 100,
      ),
  ];
  return InstrumentHistory(spec: spec, hourly: hourly, daily: daily);
}

void main() {
  test('прибыльный кандидат обыгрывает молчащий дефолт и выбирается', () async {
    // Без издержек: тест про выбор кандидата, а не про их цену.
    const optimizer = StrategyOptimizer(
      minTrainTrades: 1,
      minTestTrades: 1,
      backtester: Backtester(costs: noCosts),
    );
    final outcome = await optimizer.optimize(
      [risingHistory()],
      // Дефолт молчит, остальные кандидаты торгуют прибыльно.
      screenerBuilder: (params) =>
          StubScreener(fires: !identical(params, StrategyParams.defaults)),
    );

    expect(outcome.improved, isTrue);
    expect(outcome.testTrades, greaterThan(0));
    expect(outcome.testAvgR, greaterThan(0));
  });

  test('если дефолт не обыгран — параметры не меняются', () async {
    // Без издержек: тест про выбор кандидата, а не про их цену.
    const optimizer = StrategyOptimizer(
      minTrainTrades: 1,
      minTestTrades: 1,
      backtester: Backtester(costs: noCosts),
    );
    final outcome = await optimizer.optimize(
      [risingHistory()],
      // Торгует только дефолт: обыграть его некому.
      screenerBuilder: (params) =>
          StubScreener(fires: identical(params, StrategyParams.defaults)),
    );

    expect(outcome.improved, isFalse);
    expect(outcome.params.minScore, StrategyParams.defaults.minScore);
  });

  test('слишком короткая история — честный отказ без падения', () async {
    const optimizer = StrategyOptimizer();
    final short = InstrumentHistory(
      spec: spec,
      hourly: risingHistory().hourly.sublist(0, 90),
      daily: risingHistory().daily,
    );
    final outcome = await optimizer.optimize([short]);
    expect(outcome.improved, isFalse);
    expect(outcome.testTrades, 0);
  });

  test('параметры сериализуются в JSON и обратно', () {
    const params = StrategyParams(
      minScore: 65,
      maxEntryDistanceAtr: 0.35,
      takeProfitMultiples: [1.2, 2.0, 3.0],
    );
    final restored = StrategyParams.fromJson(params.toJson());
    expect(restored.minScore, 65);
    expect(restored.maxEntryDistanceAtr, 0.35);
    expect(restored.takeProfitMultiples, [1.2, 2.0, 3.0]);
  });
}
