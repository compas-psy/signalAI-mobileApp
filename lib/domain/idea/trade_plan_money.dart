import 'trade_plan.dart';

/// Денежная интерпретация подписанного TradePlan.
///
/// `valuePerPoint` уже является стоимостью одного пункта на единицу позиции:
/// FORTS получает её из MOEX STEPPRICE/MINSTEP на сервере, линейная crypto —
/// из risk-per-unit/ценового расстояния. Поэтому здесь нет ни одного тикера,
/// коэффициента или таблицы контрактов.
extension TradePlanMoney on TradePlan {
  /// Gross P/L в рублях при достижении одной цены всей текущей позицией.
  double pnlRublesAt(double price) {
    if (quantity <= 0 || valuePerPoint <= 0) return 0;
    final rate = quoteCurrency == 'RUB' ? 1.0 : quoteRateRub;
    if (rate == null || rate <= 0) return 0;
    final move = direction.isLong ? price - entry : entry - price;
    return move * valuePerPoint * quantity * rate;
  }

  /// Gross-прибыль, если последовательно исполнены все цели с долями плана.
  double get potentialProfitRubles {
    var total = 0.0;
    for (final target in targets) {
      total += target.fraction * pnlRublesAt(target.price);
    }
    return total > 0 ? total : 0;
  }

  /// Плановый стоп-убыток уже посчитан сервером после округления количества.
  double get potentialLossRubles => riskRubles > 0 ? riskRubles : 0;
}
