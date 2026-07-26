import 'candle.dart';

/// Механика одной сделки: лимитка → позиция → стоп/тейки/горизонт.
///
/// Единственная реализация на всё приложение: бэктест и журнал сигналов
/// (форвард-сверка бумажных сделок) проигрывают сделку одним и тем же кодом.
/// Если бы правил было два, «бумажная» статистика мерила бы не ту стратегию,
/// которую гоняет бэктест — а это прямой путь к самообману.
///
/// Правила пессимистичные и явные:
///  * лимитка исполняется, когда цена бара доходит до уровня;
///  * если бар задел и стоп, и тейк — засчитывается стоп (худший случай);
///  * тейки закрывают позицию долями, стоп добирает остаток как −1R доли;
///  * по истечении горизонта остаток закрывается ценой закрытия бара.
class PendingOrder {
  PendingOrder({
    required this.long,
    required this.entry,
    required this.stopLoss,
    required this.takeProfits,
    required this.expiresAt,
  });

  final bool long;
  final double entry;
  final double stopLoss;
  final List<({double price, double share})> takeProfits;

  /// Индекс бара, после которого неисполненная лимитка снимается.
  final int expiresAt;

  double get risk => (entry - stopLoss).abs();

  /// Дошла ли цена бара до лимитки.
  bool crossedBy(Candle bar) => long ? bar.low <= entry : bar.high >= entry;
}

class OpenPosition {
  OpenPosition({
    required this.order,
    required this.openedAt,
    required this.maxHoldBars,
  }) : remainingShare = 1;

  final PendingOrder order;
  final int openedAt;
  final int maxHoldBars;

  double remainingShare;
  double realizedR = 0;
  int nextTp = 0;

  /// Текущий незафиксированный результат позиции в R по цене [price].
  double unrealizedR(double price) {
    final risk = order.risk;
    if (risk == 0) return 0;
    final direction = order.long ? 1 : -1;
    return realizedR + remainingShare * direction * (price - order.entry) / risk;
  }

  /// Обработка бара [index]. Возвращает итог сделки в R, когда позиция закрыта.
  double? onBar(Candle bar, int index) {
    final risk = order.risk;
    final long = order.long;

    // Худший случай первым: если бар задел и стоп, и тейк — считаем стоп.
    final stopHit = long ? bar.low <= order.stopLoss : bar.high >= order.stopLoss;
    if (stopHit) {
      realizedR -= remainingShare;
      remainingShare = 0;
      return realizedR;
    }

    while (nextTp < order.takeProfits.length) {
      final tp = order.takeProfits[nextTp];
      final hit = long ? bar.high >= tp.price : bar.low <= tp.price;
      if (!hit) break;
      final r = (tp.price - order.entry).abs() / risk;
      realizedR += tp.share * r;
      remainingShare -= tp.share;
      nextTp++;
    }
    if (nextTp >= order.takeProfits.length || remainingShare <= 1e-9) {
      return realizedR;
    }

    // Горизонт свинга вышел — закрываем остаток по цене закрытия бара.
    if (index - openedAt >= maxHoldBars) {
      final r = (long ? bar.close - order.entry : order.entry - bar.close) / risk;
      realizedR += remainingShare * r;
      remainingShare = 0;
      return realizedR;
    }
    return null;
  }
}
