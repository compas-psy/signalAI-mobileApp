import 'dart:math' as math;

import '../enums.dart';

/// Параметры инструмента, нужные для расчёта денежного риска.
///
/// Для FORTS приходят из ISS (MINSTEP, STEPPRICE), для крипты задаются
/// множителем единицы и курсом котировки к рублю.
class InstrumentSpec {
  const InstrumentSpec({
    required this.id,
    required this.symbol,
    required this.name,
    required this.market,
    required this.priceDecimals,
    required this.valuePerPoint,
    required this.unitMultiplier,
    required this.unitDecimals,
    required this.unitName,
    required this.unitRiskSuffix,
    this.expiration,
    this.tickSize = 0,
  });

  final String id;
  final String symbol;
  final String name;
  final Market market;

  /// Знаков после запятой в цене инструмента.
  final int priceDecimals;

  /// Сколько рублей стоит движение цены на 1 пункт для одной единицы.
  ///
  /// FORTS: `STEPPRICE / MINSTEP`. Крипта: `unitMultiplier × курс USDT→RUB`.
  final double valuePerPoint;

  /// Размер одной торгуемой единицы: 1 контракт, 1 лот, 0,01 BTC.
  final double unitMultiplier;

  final int unitDecimals;

  /// «конт.», «лот.», «BTC».
  final String unitName;

  /// Хвост подписи риска: «контракт», «0,01 BTC», «лот (10 акций)».
  final String unitRiskSuffix;

  /// Дата экспирации — фильтр «не ближе 3 торговых дней» (ТЗ §5.4).
  final DateTime? expiration;

  /// Минимальный шаг цены. FORTS отдаёт его как `MINSTEP`; 0 означает
  /// «биржа не сказала», и тогда шаг выводится из числа знаков в цене.
  final double tickSize;

  /// Шаг цены, на который можно опереться в расчётах исполнения.
  ///
  /// Выводить шаг из [priceDecimals] можно только как запасной вариант: у RI
  /// знаков после запятой ноль, а шаг равен 10 пунктам — поэтому настоящий
  /// [tickSize] всегда в приоритете.
  double get tick =>
      tickSize > 0 ? tickSize : math.pow(10, -priceDecimals).toDouble();

  /// Сколько рублей теряется на одной единице при движении на [points].
  double riskPerUnit(double points) => points.abs() * valuePerPoint;
}
