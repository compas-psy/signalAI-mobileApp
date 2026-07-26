import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/screener.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/signal.dart';

/// Общие фикстуры прогона: подставной скринер и синтетический часовик.
///
/// Живут отдельно, потому что нужны и тесту механики исполнения, и тесту
/// издержек. Вторая копия неизбежно разъехалась бы с первой — и тесты начали
/// бы мерить разные стратегии.

/// Скринер-заглушка: «находит» сетап ровно на баре [fireAt].
///
/// Так механика лимитки, стопа и тейков проверяется отдельно от качества
/// поиска сетапов — оно покрыто `screener_test.dart`.
class FakeScreener extends Screener {
  const FakeScreener({required this.fireAt, this.risk = 2.0});

  /// Время бара, на котором «найден» сетап.
  final DateTime fireAt;

  /// Расстояние до стопа в пунктах.
  final double risk;

  @override
  ScreenerResult? evaluate(
    InstrumentInput input,
    MarketRegime regime, {
    DateTime? now,
    List<RejectedCandidate>? rejected,
  }) {
    if (input.hourly.last.time != fireAt) return null;
    final entry = input.lastPrice;
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
        for (var i = 0; i < Screener.defaultTakeProfitMultiples.length; i++)
          TakeProfit(
            index: i + 1,
            price: entry + risk * Screener.defaultTakeProfitMultiples[i],
            sharePercent: Screener.defaultTakeProfitShares[i],
          ),
      ],
      priceDecimals: 2,
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
    return ScreenerResult(signal: signal, components: components);
  }

  /// Компоненты оценки — аудит факторов агрегирует именно их.
  static const components = [
    ScoreComponent(name: 'Структура · SMC', points: 25, maxPoints: 25, note: ''),
    ScoreComponent(name: 'Режим рынка', points: 0, maxPoints: 15, note: ''),
  ];
}

/// Фьючерс: шаг цены задан явно — от него зависит модель исполнения.
const fortsSpec = InstrumentSpec(
  id: 'test',
  symbol: 'TSTU6',
  name: 'Тестовый фьючерс',
  market: Market.forts,
  priceDecimals: 2,
  valuePerPoint: 1,
  unitMultiplier: 1,
  unitDecimals: 0,
  unitName: 'конт.',
  unitRiskSuffix: 'контракт',
  tickSize: 0.01,
);

/// Перпетуал: те же цены, но издержки считаются по крипто-профилю.
const cryptoSpec = InstrumentSpec(
  id: 'testusdt',
  symbol: 'TSTUSDT',
  name: 'Тестовый перпетуал',
  market: Market.crypto,
  priceDecimals: 2,
  valuePerPoint: 1,
  unitMultiplier: 0.01,
  unitDecimals: 2,
  unitName: 'TST',
  unitRiskSuffix: '0,01 TST',
  tickSize: 0.01,
);

final fixtureStart = DateTime.utc(2026, 3, 2, 10);

Candle bar(int i, double open, double close, {double wick = 0.05}) {
  final top = close > open ? close : open;
  final bottom = close < open ? close : open;
  return Candle(
    time: fixtureStart.add(Duration(hours: i)),
    open: open,
    high: top + wick,
    low: bottom - wick,
    close: close,
    volume: 100,
  );
}

/// Часовик: 120 баров флэта на 100, затем движение с шагом [step] за бар.
List<Candle> path({required double step, int tail = 60, double wick = 0.05}) {
  final candles = <Candle>[];
  var value = 100.0;
  for (var i = 0; i < 120; i++) {
    candles.add(bar(i, value, value, wick: wick));
  }
  for (var i = 0; i < tail; i++) {
    final next = value + step;
    candles.add(bar(120 + i, value, next, wick: wick));
    value = next;
  }
  return candles;
}

/// Дневной контекст: 60 свечей до начала часовика — только чтобы пройти
/// проверки разогрева, скринер-то подставной.
List<Candle> dailyWarmup() => [
      for (var i = 60; i > 0; i--)
        Candle(
          time: fixtureStart.subtract(Duration(days: i)),
          open: 100,
          high: 101,
          low: 99,
          close: 100,
          volume: 100,
        ),
    ];

/// Дневки с заданным направлением — для шкалы режима рынка.
///
/// Зигзаг со сносом, а не прямая: структура читается по фракталам, и у
/// монотонной линии их просто нет — такая история честно распознаётся как
/// флэт. Три бара по [step]·2 вверх, три по [step] вниз: получаются
/// различимые HH/HL (или LH/LL при отрицательном шаге).
List<Candle> dailyTrend({required double step, int count = 60}) {
  final candles = <Candle>[];
  var value = 100.0;
  for (var i = 0; i < count; i++) {
    value += i % 6 < 3 ? step * 2 : -step;
    candles.add(Candle(
      time: fixtureStart.subtract(Duration(days: count - i)),
      open: value,
      high: value + 0.5,
      low: value - 0.5,
      close: value,
      volume: 100,
    ));
  }
  return candles;
}
