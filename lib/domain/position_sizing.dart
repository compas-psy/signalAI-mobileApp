import 'models/settings.dart';
import 'models/signal.dart';

/// Расчёт объёма позиции для отображения.
///
/// ВАЖНО (ТЗ §2, §6): источник истины — риск-движок на сервере. Он считает
/// объём от реальных остатков, текущего ГО и буфера свободных средств, и он же
/// резервирует маржу при подтверждении. Клиент повторяет расчёт только для
/// мгновенной реакции интерфейса на смену депозита/риска в настройках;
/// перед исполнением цифра всегда сверяется с ответом сервера.
abstract final class PositionSizing {
  /// Количество единиц инструмента: риск в рублях / риск на единицу.
  static int quantity(TradingSignal signal, RiskProfile risk) {
    if (signal.unitRisk <= 0) return 0;
    return (risk.riskRub / signal.unitRisk).floor();
  }

  /// Подпись объёма: «4 конт.», «0,03 BTC», «12 лот.».
  static String quantityLabel(TradingSignal signal, RiskProfile risk) {
    final raw = quantity(signal, risk);
    if (signal.unitMultiplier != 1) {
      final value = (raw * signal.unitMultiplier).toStringAsFixed(signal.unitDecimals);
      return '${value.replaceAll('.', ',')} ${signal.unitName}';
    }
    return '$raw ${signal.unitName}';
  }

  /// Кратность риска для тейка: «+2,1R».
  static String takeProfitR(TradingSignal signal, TakeProfit tp) {
    final risk = signal.priceRisk;
    if (risk == 0) return '';
    final r = (tp.price - signal.entry).abs() / risk;
    return '+${r.toStringAsFixed(1).replaceAll('.', ',')}R';
  }

  /// Фактический убыток при срабатывании стопа, ₽.
  ///
  /// Не равен риску из настроек: объём округляется вниз до целых единиц,
  /// поэтому реальный риск обычно чуть меньше запрошенного.
  static double potentialLossRub(TradingSignal signal, RiskProfile risk) =>
      quantity(signal, risk) * signal.unitRisk;

  /// Прибыль при исполнении всех тейков с их долями объёма, ₽.
  ///
  /// Считается по реальным ценам тейков сигнала, а не по номинальным
  /// кратностям: сколько R даёт каждый тейк — столько и учитывается.
  static double potentialProfitRub(TradingSignal signal, RiskProfile risk) {
    final priceRisk = signal.priceRisk;
    if (priceRisk == 0) return 0;
    var weightedR = 0.0;
    for (final tp in signal.takeProfits) {
      weightedR += tp.sharePercent / 100 * (tp.price - signal.entry).abs() / priceRisk;
    }
    return potentialLossRub(signal, risk) * weightedR;
  }
}
