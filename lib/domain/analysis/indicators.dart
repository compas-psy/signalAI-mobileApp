import 'dart:math' as math;

import 'candle.dart';

/// Детерминированные индикаторы первой ступени скринера (ТЗ §5.1).
///
/// Никакой магии и никаких «примерно»: всё считается по классическим формулам,
/// чтобы результат был воспроизводим и проверяем тестами.

/// RSI(14) по Уайлдеру.
///
/// Возвращает список той же длины, что и [closes]; первые `period` значений —
/// null, потому что данных для расчёта ещё нет.
List<double?> rsiWilder(List<double> closes, {int period = 14}) {
  final result = List<double?>.filled(closes.length, null);
  if (closes.length <= period) return result;

  var gain = 0.0;
  var loss = 0.0;
  for (var i = 1; i <= period; i++) {
    final change = closes[i] - closes[i - 1];
    if (change >= 0) {
      gain += change;
    } else {
      loss -= change;
    }
  }
  var avgGain = gain / period;
  var avgLoss = loss / period;
  result[period] = _rsiFrom(avgGain, avgLoss);

  for (var i = period + 1; i < closes.length; i++) {
    final change = closes[i] - closes[i - 1];
    final up = change > 0 ? change : 0.0;
    final down = change < 0 ? -change : 0.0;
    // Сглаживание Уайлдера, а не простое среднее.
    avgGain = (avgGain * (period - 1) + up) / period;
    avgLoss = (avgLoss * (period - 1) + down) / period;
    result[i] = _rsiFrom(avgGain, avgLoss);
  }
  return result;
}

double _rsiFrom(double avgGain, double avgLoss) {
  if (avgLoss == 0) return avgGain == 0 ? 50 : 100;
  final rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

/// ATR(14) по Уайлдеру — используется для реалистичности целей и буфера стопа.
List<double?> atr(List<Candle> candles, {int period = 14}) {
  final result = List<double?>.filled(candles.length, null);
  if (candles.length <= period) return result;

  final trueRanges = <double>[0];
  for (var i = 1; i < candles.length; i++) {
    final c = candles[i];
    final prevClose = candles[i - 1].close;
    trueRanges.add(math.max(
      c.high - c.low,
      math.max((c.high - prevClose).abs(), (c.low - prevClose).abs()),
    ));
  }

  var sum = 0.0;
  for (var i = 1; i <= period; i++) {
    sum += trueRanges[i];
  }
  var value = sum / period;
  result[period] = value;
  for (var i = period + 1; i < candles.length; i++) {
    value = (value * (period - 1) + trueRanges[i]) / period;
    result[i] = value;
  }
  return result;
}

/// Экстремум структуры: свинг-хай или свинг-лоу.
class SwingPoint {
  const SwingPoint({required this.index, required this.price, required this.isHigh});

  final int index;
  final double price;
  final bool isHigh;
}

/// Фрактальные свинги: экстремум, который выше (ниже) [depth] соседей с обеих
/// сторон. Основа для структуры рынка и поиска BOS/CHoCH.
List<SwingPoint> swingPoints(List<Candle> candles, {int depth = 2}) {
  final points = <SwingPoint>[];
  for (var i = depth; i < candles.length - depth; i++) {
    var isHigh = true;
    var isLow = true;
    for (var j = 1; j <= depth; j++) {
      if (candles[i].high <= candles[i - j].high || candles[i].high <= candles[i + j].high) {
        isHigh = false;
      }
      if (candles[i].low >= candles[i - j].low || candles[i].low >= candles[i + j].low) {
        isLow = false;
      }
    }
    if (isHigh) points.add(SwingPoint(index: i, price: candles[i].high, isHigh: true));
    if (isLow) points.add(SwingPoint(index: i, price: candles[i].low, isHigh: false));
  }
  return points;
}

/// Направление рыночной структуры.
enum StructureTrend { up, down, flat }

/// Тип слома структуры.
enum StructureBreak {
  /// Продолжение тренда: пробит экстремум по направлению тренда.
  bos,

  /// Слом характера: пробит экстремум против тренда.
  choch,

  none,
}

/// Состояние структуры рынка: тренд, факт слома и уровень, на котором он был.
class MarketStructure {
  const MarketStructure({
    required this.trend,
    required this.breakType,
    required this.breakLevel,
    required this.lastSwingHigh,
    required this.lastSwingLow,
  });

  final StructureTrend trend;
  final StructureBreak breakType;
  final double? breakLevel;
  final SwingPoint? lastSwingHigh;
  final SwingPoint? lastSwingLow;
}

/// Разбор структуры: HH/HL против LH/LL и слом последнего экстремума.
MarketStructure analyzeStructure(List<Candle> candles, {int depth = 2}) {
  final points = swingPoints(candles, depth: depth);
  final highs = points.where((p) => p.isHigh).toList();
  final lows = points.where((p) => !p.isHigh).toList();

  if (highs.length < 2 || lows.length < 2) {
    return const MarketStructure(
      trend: StructureTrend.flat,
      breakType: StructureBreak.none,
      breakLevel: null,
      lastSwingHigh: null,
      lastSwingLow: null,
    );
  }

  final higherHigh = highs.last.price > highs[highs.length - 2].price;
  final higherLow = lows.last.price > lows[lows.length - 2].price;
  final trend = switch ((higherHigh, higherLow)) {
    (true, true) => StructureTrend.up,
    (false, false) => StructureTrend.down,
    _ => StructureTrend.flat,
  };

  final lastHigh = highs.last;
  final lastLow = lows.last;
  final close = candles.last.close;

  var breakType = StructureBreak.none;
  double? breakLevel;
  if (close > lastHigh.price) {
    // Пробой вверх — продолжение растущей структуры либо слом падающей.
    breakType = trend == StructureTrend.down ? StructureBreak.choch : StructureBreak.bos;
    breakLevel = lastHigh.price;
  } else if (close < lastLow.price) {
    breakType = trend == StructureTrend.up ? StructureBreak.choch : StructureBreak.bos;
    breakLevel = lastLow.price;
  }

  return MarketStructure(
    trend: trend,
    breakType: breakType,
    breakLevel: breakLevel,
    lastSwingHigh: lastHigh,
    lastSwingLow: lastLow,
  );
}

/// Незакрытый гэп ликвидности (fair value gap) — трёхсвечная формация.
class FairValueGap {
  const FairValueGap({
    required this.index,
    required this.from,
    required this.to,
    required this.bullish,
  });

  final int index;
  final double from;
  final double to;
  final bool bullish;

  double get mid => (from + to) / 2;
}

/// Поиск FVG: у бычьего минимум третьей свечи выше максимума первой.
List<FairValueGap> fairValueGaps(List<Candle> candles) {
  final gaps = <FairValueGap>[];
  for (var i = 2; i < candles.length; i++) {
    final first = candles[i - 2];
    final third = candles[i];
    if (third.low > first.high) {
      gaps.add(FairValueGap(index: i, from: first.high, to: third.low, bullish: true));
    } else if (third.high < first.low) {
      gaps.add(FairValueGap(index: i, from: third.high, to: first.low, bullish: false));
    }
  }
  return gaps;
}

/// Формализованные свечные паттерны (ТЗ §5.1).
abstract final class CandlePatterns {
  /// Пин-бар: маленькое тело и длинная тень с одной стороны.
  static bool isPinBar(Candle c, {bool bullish = true}) {
    if (c.range <= 0) return false;
    if (c.body > c.range * 0.34) return false;
    final wick = bullish ? c.lowerWick : c.upperWick;
    final opposite = bullish ? c.upperWick : c.lowerWick;
    return wick >= c.range * 0.6 && wick > opposite * 2;
  }

  /// Поглощение: тело текущей свечи перекрывает тело предыдущей.
  static bool isEngulfing(Candle previous, Candle current, {bool bullish = true}) {
    if (bullish) {
      if (!current.isUp || previous.isUp) return false;
      return current.close >= previous.open && current.open <= previous.close;
    }
    if (current.isUp || !previous.isUp) return false;
    return current.close <= previous.open && current.open >= previous.close;
  }

  /// Внутренний бар: диапазон целиком внутри предыдущего.
  static bool isInsideBar(Candle previous, Candle current) =>
      current.high < previous.high && current.low > previous.low;
}

/// Z-оценка последнего значения относительно окна — для аномалий объёма.
double? zScore(List<double> values, {int lookback = 20}) {
  if (values.length < lookback + 1) return null;
  final window = values.sublist(values.length - lookback - 1, values.length - 1);
  final mean = window.reduce((a, b) => a + b) / window.length;
  final variance =
      window.map((v) => (v - mean) * (v - mean)).reduce((a, b) => a + b) / window.length;
  final std = math.sqrt(variance);
  if (std == 0) return null;
  return (values.last - mean) / std;
}

/// Тип дивергенции цены и RSI.
enum Divergence { bullish, bearish, none }

/// Поиск дивергенции по двум последним свингам: цена делает новый экстремум,
/// а RSI — нет.
Divergence findDivergence(List<Candle> candles, List<double?> rsi, {int depth = 2}) {
  final points = swingPoints(candles, depth: depth);
  final lows = points.where((p) => !p.isHigh).toList();
  final highs = points.where((p) => p.isHigh).toList();

  if (lows.length >= 2) {
    final a = lows[lows.length - 2];
    final b = lows.last;
    final rsiA = rsi[a.index];
    final rsiB = rsi[b.index];
    if (rsiA != null && rsiB != null && b.price < a.price && rsiB > rsiA) {
      return Divergence.bullish;
    }
  }
  if (highs.length >= 2) {
    final a = highs[highs.length - 2];
    final b = highs.last;
    final rsiA = rsi[a.index];
    final rsiB = rsi[b.index];
    if (rsiA != null && rsiB != null && b.price > a.price && rsiB < rsiA) {
      return Divergence.bearish;
    }
  }
  return Divergence.none;
}
