/// Свеча OHLCV с открытым интересом.
///
/// Общая форма для обоих источников: MOEX ISS отдаёт OI в поле OPENPOSITION,
/// Bybit — отдельным эндпоинтом open-interest, оба кладутся сюда.
class Candle {
  const Candle({
    required this.time,
    required this.open,
    required this.high,
    required this.low,
    required this.close,
    required this.volume,
    this.openInterest,
  });

  final DateTime time;
  final double open;
  final double high;
  final double low;
  final double close;
  final double volume;

  /// Открытый интерес на конец свечи. null — если источник его не отдал.
  final double? openInterest;

  double get body => (close - open).abs();
  double get range => high - low;
  bool get isUp => close >= open;

  /// Верхняя тень.
  double get upperWick => high - (close > open ? close : open);

  /// Нижняя тень.
  double get lowerWick => (close > open ? open : close) - low;

  @override
  String toString() => 'Candle(${time.toIso8601String()} O$open H$high L$low C$close V$volume)';
}

/// Таймфрейм анализа (ТЗ §3.1: 10м — уровни, 1H и 1D — структура).
enum Timeframe {
  m10(minutes: 10),
  h1(minutes: 60),
  h4(minutes: 240),
  d1(minutes: 1440);

  const Timeframe({required this.minutes});

  final int minutes;
}
