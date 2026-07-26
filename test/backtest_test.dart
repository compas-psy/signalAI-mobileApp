import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/backtester.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/screener.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/signal.dart';

/// Движок исполнения тестируется отдельно от поиска сетапов: скринер покрыт
/// `screener_test.dart`, а здесь подставной скринер стреляет в заданный бар —
/// и проверяется механика лимитки, стопа, частичных тейков и агрегатов.
class FakeScreener extends Screener {
  const FakeScreener({required this.fireAt});

  /// Время бара, на котором «найден» сетап.
  final DateTime fireAt;

  @override
  ScreenerResult? evaluate(
    InstrumentInput input,
    MarketRegime regime, {
    DateTime? now,
    List<RejectedCandidate>? rejected,
  }) {
    if (input.hourly.last.time != fireAt) return null;
    final entry = input.lastPrice;
    const risk = 2.0;
    final signal = TradingSignal(
      id: 'fake',
      symbol: input.spec.symbol,
      name: input.spec.name,
      market: input.spec.market,
      direction: Direction.long,
      horizon: Horizon.swing,
      horizonLabel: '1–5 дней',
      score: 80,
      entry: entry,
      stopLoss: entry - risk,
      takeProfits: [
        for (var i = 0; i < Screener.takeProfitMultiples.length; i++)
          TakeProfit(
            index: i + 1,
            price: entry + risk * Screener.takeProfitMultiples[i],
            sharePercent: Screener.takeProfitShares[i],
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
    );
    return ScreenerResult(signal: signal, components: const []);
  }
}

const spec = InstrumentSpec(
  id: 'test',
  symbol: 'TSTU6',
  name: 'Тестовый фьючерс',
  market: Market.forts,
  priceDecimals: 0,
  valuePerPoint: 1,
  unitMultiplier: 1,
  unitDecimals: 0,
  unitName: 'конт.',
  unitRiskSuffix: 'контракт',
);

final start = DateTime.utc(2026, 3, 2, 10);

Candle bar(int i, double open, double close) {
  final top = close > open ? close : open;
  final bottom = close < open ? close : open;
  return Candle(
    time: start.add(Duration(hours: i)),
    open: open,
    high: top + 0.05,
    low: bottom - 0.01,
    close: close,
    volume: 100,
  );
}

/// Часовик: 120 баров флэта на 100, затем движение с шагом [step] за бар.
List<Candle> path({required double step, int tail = 60}) {
  final candles = <Candle>[];
  var value = 100.0;
  for (var i = 0; i < 120; i++) {
    candles.add(bar(i, value, value));
  }
  for (var i = 0; i < tail; i++) {
    final next = value + step;
    candles.add(bar(120 + i, value, next));
    value = next;
  }
  return candles;
}

/// Дневной контекст: 60 свечей до начала часовика — только чтобы пройти
/// проверки разогрева, скринер-то подставной.
List<Candle> dailyWarmup() => [
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

void main() {
  // Бар 120 — момент сигнала: индекс кратен шагу оценки (по умолчанию 4).
  final fireAt = start.add(const Duration(hours: 120));

  test('лимитка исполняется, тейки собираются по долям объёма', () async {
    final summary = await Backtester(screener: FakeScreener(fireAt: fireAt)).run([
      InstrumentHistory(spec: spec, hourly: path(step: 0.5), daily: dailyWarmup()),
    ]);

    expect(summary.count, 1);
    // 50% на 1,4R + 30% на 2,2R + 20% на 3,5R = 2,06R.
    expect(summary.trades.single.resultR, closeTo(2.06, 1e-9));
    expect(summary.winRate, 100);
    expect(summary.profitFactor, isNull, reason: 'убытков не было — PF не определён');
    expect(summary.equityCurve.single, closeTo(2.06, 1e-9));
  });

  test('стоп срабатывает по худшему случаю и даёт −1R', () async {
    final summary = await Backtester(screener: FakeScreener(fireAt: fireAt)).run([
      InstrumentHistory(spec: spec, hourly: path(step: -0.5), daily: dailyWarmup()),
    ]);

    expect(summary.count, 1);
    expect(summary.trades.single.resultR, closeTo(-1, 1e-9));
    expect(summary.winRate, 0);
  });

  test('боковик после сигнала закрывается по горизонту около нуля', () async {
    final summary = await Backtester(
      screener: FakeScreener(fireAt: fireAt),
      maxHoldBars: 24,
    ).run([
      InstrumentHistory(spec: spec, hourly: path(step: 0, tail: 80), daily: dailyWarmup()),
    ]);

    expect(summary.count, 1);
    expect(summary.trades.single.resultR.abs(), lessThan(0.1));
  });

  test('пустой список инструментов не падает', () async {
    final summary = await const Backtester().run(const []);
    expect(summary.count, 0);
    expect(summary.profitFactor, isNull);
  });
}
