import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/indicators.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/screener.dart';
import 'package:signalai/domain/enums.dart';

/// Фьючерс с шагом цены 1 и стоимостью шага 1 ₽ — считать риск просто.
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

Candle bar(int i, double open, double close, {double volume = 100}) {
  final top = close > open ? close : open;
  final bottom = close < open ? close : open;
  final up = close >= open;
  // Тень по направлению свечи длиннее противоположной. Это не украшательство:
  // при одинаковых тенях максимум разворотной свечи в точности равен максимуму
  // предыдущей (её открытие — это её закрытие), и фрактальный поиск свингов,
  // требующий строгого неравенства, не находит ни одного экстремума.
  return Candle(
    time: DateTime.utc(2026, 7, 1).add(Duration(hours: i)),
    open: open,
    high: top + (up ? 0.05 : 0.01),
    low: bottom - (up ? 0.01 : 0.05),
    close: close,
    volume: volume,
  );
}

/// Растущая пила HH/HL: восемь колен, откат и пробой последнего максимума
/// с закрытием чуть выше — то есть свежий BOS и ретест зоны входа.
List<Candle> risingWithBreakout() {
  final closes = <double>[];
  var value = 100.0;
  for (var leg = 0; leg < 8; leg++) {
    for (var i = 0; i < 5; i++) {
      value += 1.0;
      closes.add(value);
    }
    for (var i = 0; i < 3; i++) {
      value -= 0.8;
      closes.add(value);
    }
  }
  // Последнее колено вверх — этот максимум и станет уровнем слома.
  for (var i = 0; i < 5; i++) {
    value += 1.0;
    closes.add(value);
  }
  final swingHigh = value;
  // Откат тремя свечами.
  for (var i = 0; i < 3; i++) {
    value -= 0.8;
    closes.add(value);
  }
  // Подход к уровню и пробой с закрытием выше максимума.
  closes.add(swingHigh - 1.2);
  closes.add(swingHigh + 0.3);

  final candles = <Candle>[];
  for (var i = 0; i < closes.length; i++) {
    final open = i == 0 ? closes[i] : closes[i - 1];
    // Пробойная свеча идёт на всплеске объёма — как и должно быть у рабочего
    // слома структуры.
    final breakout = i == closes.length - 1;
    candles.add(bar(i, open, closes[i], volume: breakout ? 420 : 100 + (i % 4) * 10));
  }
  return candles;
}

List<Candle> dailyContext() {
  final candles = <Candle>[];
  var value = 90.0;
  for (var i = 0; i < 40; i++) {
    final open = value;
    value += i.isEven ? 1.5 : -0.5;
    candles.add(bar(i, open, value));
  }

  return candles;
}

void main() {
  const screener = Screener();
  const bullishRegime = MarketRegime(
    indexTrend: StructureTrend.up,
    currencyTrend: StructureTrend.up,
    cryptoTrend: StructureTrend.up,
  );

  InstrumentInput input({
    double? openInterestChange,
    double? funding,
    DateTime? expiration,
    List<Candle>? hourly,
  }) {
    final bars = hourly ?? risingWithBreakout();
    return InstrumentInput(
      spec: expiration == null
          ? spec
          : InstrumentSpec(
              id: spec.id,
              symbol: spec.symbol,
              name: spec.name,
              market: spec.market,
              priceDecimals: spec.priceDecimals,
              valuePerPoint: spec.valuePerPoint,
              unitMultiplier: spec.unitMultiplier,
              unitDecimals: spec.unitDecimals,
              unitName: spec.unitName,
              unitRiskSuffix: spec.unitRiskSuffix,
              expiration: expiration,
            ),
      hourly: bars,
      daily: dailyContext(),
      lastPrice: bars.last.close,
      changePercentLabel: '+0,84%',
      changeUp: true,
      openInterestChangePercent: openInterestChange,
      fundingRate: funding,
    );
  }

  test('на растущей структуре с пробоем появляется лонг', () {
    final rejected = <RejectedCandidate>[];
    final result = screener.evaluate(input(), bullishRegime, rejected: rejected);

    expect(result, isNotNull, reason: rejected.map((r) => r.reason).join('; '));
    final signal = result!.signal;
    expect(signal.direction, Direction.long);
    expect(signal.stopLoss, lessThan(signal.entry));
    expect(signal.takeProfits, hasLength(3));
    expect(signal.takeProfits.first.price, greaterThan(signal.entry));
  });

  test('уровни расставлены по кратностям риска', () {
    final signal = screener.evaluate(input(), bullishRegime)!.signal;
    final risk = signal.riskPerUnit;

    expect((signal.takeProfits[0].price - signal.entry) / risk, closeTo(1.4, 1e-6));
    expect((signal.takeProfits[1].price - signal.entry) / risk, closeTo(2.2, 1e-6));
    expect((signal.takeProfits[2].price - signal.entry) / risk, closeTo(3.5, 1e-6));
    expect(signal.riskReward, '2,2');
    expect(signal.takeProfits.map((t) => t.sharePercent), [50, 30, 20]);
  });

  test('денежный риск на контракт считается через стоимость пункта', () {
    final signal = screener.evaluate(input(), bullishRegime)!.signal;
    expect(signal.unitRisk, closeTo(signal.riskPerUnit * spec.valuePerPoint, 1e-6));
  });

  test('оценка складывается из шести блоков и нормируется на 100', () {
    final result = screener.evaluate(input(), bullishRegime)!;
    expect(result.components, hasLength(6));
    expect(result.signal.factors, hasLength(6));
    expect(result.signal.score, inInclusiveRange(60, 100));

    final points = result.components.fold<double>(0, (sum, c) => sum + c.points);
    expect(result.signal.score, (points / Screener.maxPoints * 100).round());
  });

  test('идея против режима рынка теряет блок режима', () {
    const bearish = MarketRegime(
      indexTrend: StructureTrend.down,
      currencyTrend: StructureTrend.down,
      cryptoTrend: StructureTrend.down,
    );
    final against = screener.evaluate(input(), bearish);
    final along = screener.evaluate(input(), bullishRegime);

    expect(against?.signal.score ?? 0, lessThan(along!.signal.score));
  });

  test('фандинг и рост OI добавляют баллы', () {
    final plain = screener.evaluate(input(), bullishRegime)!;
    final withFlow = screener.evaluate(
      input(openInterestChange: 4.2, funding: -0.00008),
      bullishRegime,
    )!;

    expect(withFlow.signal.score, greaterThan(plain.signal.score));
    expect(withFlow.signal.chips, contains('OI +4.2%'));
  });

  test('близкая экспирация отсекает кандидата', () {
    final rejected = <RejectedCandidate>[];
    final result = screener.evaluate(
      input(expiration: DateTime.utc(2026, 7, 3)),
      bullishRegime,
      now: DateTime.utc(2026, 7, 2),
      rejected: rejected,
    );

    expect(result, isNull);
    expect(rejected.single.reason, contains('экспирации'));
  });

  test('короткой истории недостаточно', () {
    final rejected = <RejectedCandidate>[];
    final result = screener.evaluate(
      input(hourly: risingWithBreakout().take(20).toList()),
      bullishRegime,
      rejected: rejected,
    );

    expect(result, isNull);
    expect(rejected.single.reason, 'мало истории');
  });

  test('порог пуша — 75, ниже идея остаётся без уведомления', () {
    final result = screener.evaluate(input(openInterestChange: 4.2), bullishRegime)!;
    final expected =
        result.signal.score >= 75 ? SignalStatus.pushed : SignalStatus.proposed;
    expect(result.signal.status, expected);
  });
}
