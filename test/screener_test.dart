import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/indicators.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/factor_audit.dart';
import 'package:signalai/domain/analysis/optimizer.dart';
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

  test('график сигнала — реальные свечи входных данных, не заготовка', () {
    final bars = risingWithBreakout();
    final signal = screener.evaluate(input(hourly: bars), bullishRegime)!.signal;
    final chart = signal.chart;

    expect(chart, isNotNull);
    expect(chart!.timeframeLabel, '1H');
    // Окно усечено, но это хвост входной истории: последняя свеча графика —
    // последняя свеча данных, свеча в свечу.
    expect(chart.candles.length, lessThanOrEqualTo(72));
    expect(chart.candles.last.close, bars.last.close);
    expect(chart.candles.last.high, bars.last.high);
    expect(
      chart.candles.first.close,
      bars[bars.length - chart.candles.length].close,
    );
    // Пробой в фикстуре есть — слом обязан быть размечен, и по реальной цене.
    expect(chart.breakLabel, isNotNull);
    expect(chart.breakLevel, signal.entry);
  });

  test('уровни расставлены по кратностям риска', () {
    final signal = screener.evaluate(input(), bullishRegime)!.signal;
    final risk = signal.priceRisk;

    expect((signal.takeProfits[0].price - signal.entry) / risk, closeTo(1.4, 1e-6));
    expect((signal.takeProfits[1].price - signal.entry) / risk, closeTo(2.2, 1e-6));
    expect((signal.takeProfits[2].price - signal.entry) / risk, closeTo(3.5, 1e-6));
    expect(signal.riskReward, '2,2');
    expect(signal.takeProfits.map((t) => t.sharePercent), [50, 30, 20]);
  });

  test('денежный риск на контракт считается через стоимость пункта', () {
    final signal = screener.evaluate(input(), bullishRegime)!.signal;
    expect(signal.unitRisk, closeTo(signal.priceRisk * spec.valuePerPoint, 1e-6));
  });

  test('оценка складывается из шести блоков и нормируется на 100', () {
    final result = screener.evaluate(input(), bullishRegime)!;
    expect(result.components, hasLength(6));
    expect(result.signal.factors, hasLength(6));
    expect(result.signal.score, inInclusiveRange(60, 100));

    final points = result.components.fold<double>(0, (sum, c) => sum + c.points);
    expect(result.signal.score, (points / Screener.maxPoints * 100).round());
  });

  test('идея против режима рынка отбрасывается, а не штрафуется', () {
    // Раньше контртрендовая идея теряла блок режима, но могла набрать порог
    // на других блоках — и именно такие сделки тянули профит-фактор вниз.
    // Свинг против старшего тренда — ставка на разворот; мы её не торгуем.
    const bearish = MarketRegime(
      indexTrend: StructureTrend.down,
      currencyTrend: StructureTrend.down,
      cryptoTrend: StructureTrend.down,
    );
    final rejected = <RejectedCandidate>[];
    final against = screener.evaluate(input(), bearish, rejected: rejected);

    expect(against, isNull);
    expect(rejected.single.reason, contains('против'));
    expect(screener.evaluate(input(), bullishRegime), isNotNull,
        reason: 'по тренду идея остаётся');
  });

  test('нейтральный режим идею не отбрасывает', () {
    final result = screener.evaluate(input(), MarketRegime.unknown);
    expect(result, isNotNull, reason: 'во флэте у структуры 1H есть право голоса');
  });

  test('история фактора дописывается в обоснование', () {
    const edges = [
      FactorEdge(
        factor: 'Структура · SMC',
        withCount: 41,
        withoutCount: 35,
        withAvgR: 0.18,
        withoutAvgR: -0.14,
      ),
      // Малой выборке слова не даются — это шум, а не знание.
      FactorEdge(
        factor: 'RSI(14)',
        withCount: 4,
        withoutCount: 3,
        withAvgR: 2,
        withoutAvgR: -2,
      ),
    ];
    final result = screener.evaluate(input(), bullishRegime, factorHistory: edges);

    final structure =
        result!.signal.factors.firstWhere((f) => f.name == 'Структура · SMC');
    expect(structure.text, contains('история: +0,32R/сделку'));
    expect(structure.text, contains('41 сделок'));

    final rsi = result.signal.factors.firstWhere((f) => f.name == 'RSI(14)');
    expect(rsi.text, isNot(contains('история')),
        reason: 'семь сделок — не выборка');
  });

  test('фильтр триггерной свечи отбрасывает вход без реакции цены', () {
    // Пробойная свеча фикстуры — обычный импульс, не пин-бар и не поглощение.
    // С фильтром такой ретест не торгуется, и причина видна в отбраковках.
    const strict = Screener(requireTrigger: true);
    final rejected = <RejectedCandidate>[];

    expect(strict.evaluate(input(), bullishRegime, rejected: rejected), isNull);
    expect(rejected.single.reason, contains('триггерной свечи'));
    expect(screener.evaluate(input(), bullishRegime), isNotNull,
        reason: 'без фильтра тот же кандидат проходит');
  });

  test('стоп-вход ставит уровень за пробойным баром, а не на ретесте', () {
    const breaker = Screener(entryType: EntryType.stopBreak);
    // После пробоя — мелкий откат и новый толчок: у стоп-входа есть свежая
    // база, за которую ставится стоп.
    final base = risingWithBreakout();
    final top = base.last.close;
    final extra = [top - 0.3, top - 0.6, top - 0.5, top - 0.2, top + 0.4, top + 0.2, top + 0.5];
    final bars = [
      ...base,
      for (var i = 0; i < extra.length; i++)
        bar(base.length + i, i == 0 ? top : extra[i - 1], extra[i]),
    ];
    // Шаг цены реалистичный: тик размером с ATR отодвинул бы вход за предел
    // допустимой дистанции, чего с настоящими контрактами не бывает.
    const fine = InstrumentSpec(
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
      tickSize: 0.01,
    );
    final probe = InstrumentInput(
      spec: fine,
      hourly: bars,
      daily: dailyContext(),
      lastPrice: bars.last.close,
      changePercentLabel: '+0,84%',
      changeUp: true,
    );
    final signal = breaker.evaluate(probe, bullishRegime)!.signal;

    // Лонг: вход выше максимума последнего бара — сделка открывается только
    // продолжением движения, а не откатом к уровню.
    expect(signal.entryIsStop, isTrue);
    expect(signal.entry, greaterThan(bars.last.high));
    expect(signal.stopLoss, lessThan(signal.entry));
    // Стоп за базой пробоя, а не за дальним свингом: риск остаётся в рамках.
    expect(signal.entry - signal.stopLoss, lessThan(3));

    final retest = screener.evaluate(input(), bullishRegime)!.signal;
    expect(retest.entryIsStop, isFalse);
  });

  test('ресемпл 4H склеивает часовики свеча в свечу', () {
    final hourly = [
      for (var i = 0; i < 8; i++) bar(i, 100.0 + i, 101.0 + i),
    ];
    final fourH = resampleHours(hourly, 4);

    expect(fourH, hasLength(2));
    expect(fourH.first.open, hourly.first.open);
    expect(fourH.first.close, hourly[3].close);
    expect(fourH.first.high,
        [for (final c in hourly.take(4)) c.high].reduce((a, b) => a > b ? a : b));
    expect(fourH.first.low,
        [for (final c in hourly.take(4)) c.low].reduce((a, b) => a < b ? a : b));
    expect(fourH.last.close, hourly.last.close);
  });

  test('сетка подбора содержит структурные варианты входа', () {
    expect(StrategyOptimizer.grid.any((p) => p.entryType == EntryType.stopBreak), isTrue);
    expect(StrategyOptimizer.grid.any((p) => p.align4h), isTrue);
    expect(StrategyOptimizer.grid.first, StrategyParams.defaults);
  });

  test('план ведения написан в самой идее', () {
    final result = screener.evaluate(input(), bullishRegime);
    expect(result!.signal.note, contains('безубыток'));
    expect(result.signal.note, contains('50/30/20'));
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
