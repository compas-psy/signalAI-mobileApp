import '../enums.dart';
import 'candle.dart';
import 'instrument_spec.dart';

/// Издержки сделки в единицах цены инструмента.
///
/// Считаются явно и пессимистично: бэктест без комиссий — это не бэктест, а
/// реклама. При средней сделке в +0,1…0,3R комиссия и проскальзывание решают,
/// есть эдж или его нет.
class TradingCosts {
  const TradingCosts({
    this.feeRate = 0,
    this.feePoints = 0,
    this.slippage = 0,
    this.fundingPerBar = 0,
  });

  /// Комиссия долей от цены за одну сторону: 0,00055 = 0,055%.
  final double feeRate;

  /// Фиксированный сбор за одну сторону, в пунктах цены.
  final double feePoints;

  /// Проскальзывание одной стороны при исполнении **по рынку**, в пунктах.
  ///
  /// К лимитным исполнениям (вход и тейки) не применяется: лимитка даёт свою
  /// цену или лучше. Применяется к стопу и к закрытию по горизонту.
  final double slippage;

  /// Фандинг за один час удержания, долей от цены.
  final double fundingPerBar;

  static const none = TradingCosts();

  /// Комиссия одной стороны при исполнении по цене [price].
  double fee(double price) => feeRate * price.abs() + feePoints;

  Map<String, dynamic> toJson() => {
        'fee_rate': feeRate,
        'fee_points': feePoints,
        'slippage': slippage,
        'funding_per_bar': fundingPerBar,
      };

  factory TradingCosts.fromJson(Map<String, dynamic> j) => TradingCosts(
        feeRate: (j['fee_rate'] as num?)?.toDouble() ?? 0,
        feePoints: (j['fee_points'] as num?)?.toDouble() ?? 0,
        slippage: (j['slippage'] as num?)?.toDouble() ?? 0,
        fundingPerBar: (j['funding_per_bar'] as num?)?.toDouble() ?? 0,
      );
}

/// Издержки по умолчанию для инструмента.
///
/// Крипта: Bybit perp, taker 0,055% за сторону (пессимистично — вход лимиткой
/// на деле был бы maker), проскальзывание в один тик на рыночных выходах и
/// фандинг 0,01% за восьмичасовой период. Исторического фандинга по каждому
/// бару у нас нет, поэтому берётся консервативная константа **против**
/// позиции в любую сторону — так прогон не может выиграть на фандинге.
///
/// FORTS: биржевой сбор и брокерская комиссия — порядка рубля на контракт за
/// сторону, переводится в пункты через стоимость пункта; проскальзывание —
/// один шаг цены.
TradingCosts defaultCostsFor(InstrumentSpec spec) => switch (spec.market) {
      Market.crypto => TradingCosts(
          feeRate: 0.00055,
          slippage: spec.tick,
          fundingPerBar: 0.0001 / 8,
        ),
      Market.forts || Market.moex => TradingCosts(
          feePoints: spec.valuePerPoint > 0 ? 1 / spec.valuePerPoint : 0,
          slippage: spec.tick,
        ),
    };

/// Прогон без издержек — для тестов механики, где издержки только мешают.
TradingCosts noCosts(InstrumentSpec spec) => TradingCosts.none;

/// Механика одной сделки: лимитка → позиция → стоп/тейки/горизонт.
///
/// Единственная реализация на всё приложение: бэктест и журнал сигналов
/// (форвард-сверка бумажных сделок) проигрывают сделку одним и тем же кодом.
/// Если бы правил было два, «бумажная» статистика мерила бы не ту стратегию,
/// которую гоняет бэктест — а это прямой путь к самообману.
///
/// Правила пессимистичные и явные:
///  * лимитка (вход и тейки) исполняется, только когда цена прошла **за**
///    уровень минимум на [fillMargin]: касание в тик очередь не проливает;
///  * стоп срабатывает по касанию — так он и работает на бирже, и это худший
///    случай для нас — и исполняется хуже уровня на величину проскальзывания;
///  * если бар задел и стоп, и тейк — засчитывается стоп;
///  * тейки закрывают позицию долями, стоп добирает остаток;
///  * по истечении горизонта остаток закрывается по рынку ценой закрытия бара;
///  * комиссия списывается на входе и на каждом выходе, фандинг — каждый бар.
class PendingOrder {
  PendingOrder({
    required this.long,
    required this.entry,
    required this.stopLoss,
    required this.takeProfits,
    required this.expiresAt,
    this.costs = TradingCosts.none,
    this.fillMargin = 0,
  });

  final bool long;
  final double entry;
  final double stopLoss;
  final List<({double price, double share})> takeProfits;

  /// Индекс бара, после которого неисполненная лимитка снимается.
  final int expiresAt;

  final TradingCosts costs;

  /// Насколько цена должна пройти за лимитный уровень, чтобы считать сделку
  /// исполненной. Обычно один шаг цены.
  final double fillMargin;

  double get risk => (entry - stopLoss).abs();

  /// Прошла ли цена бара за лимитку.
  bool crossedBy(Candle bar) =>
      long ? bar.low <= entry - fillMargin : bar.high >= entry + fillMargin;
}

class OpenPosition {
  OpenPosition({
    required this.order,
    required this.openedAt,
    required this.maxHoldBars,
  }) : remainingShare = 1 {
    // Комиссия входа списывается сразу: позиция уже стоила денег.
    _charge(order.costs.fee(order.entry));
  }

  final PendingOrder order;
  final int openedAt;
  final int maxHoldBars;

  double remainingShare;
  double realizedR = 0;
  int nextTp = 0;

  /// Сколько R съели издержки — чтобы можно было показать их отдельно,
  /// а не прятать внутри итога.
  double costR = 0;

  /// Текущий незафиксированный результат позиции в R по цене [price].
  double unrealizedR(double price) {
    final risk = order.risk;
    if (risk == 0) return 0;
    final direction = order.long ? 1 : -1;
    return realizedR + remainingShare * direction * (price - order.entry) / risk;
  }

  /// Обработка бара [index]. Возвращает итог сделки в R, когда позиция закрыта.
  double? onBar(Candle bar, int index) {
    final long = order.long;
    final costs = order.costs;

    // Фандинг за час удержания — с остатка позиции, против нас.
    if (costs.fundingPerBar != 0) {
      _charge(remainingShare * costs.fundingPerBar * order.entry.abs());
    }

    // Худший случай первым: если бар задел и стоп, и тейк — считаем стоп.
    final stopHit = long ? bar.low <= order.stopLoss : bar.high >= order.stopLoss;
    if (stopHit) {
      final exit = long ? order.stopLoss - costs.slippage : order.stopLoss + costs.slippage;
      _exitAt(exit, remainingShare, slippage: costs.slippage);
      return realizedR;
    }

    while (nextTp < order.takeProfits.length) {
      final tp = order.takeProfits[nextTp];
      final hit = long
          ? bar.high >= tp.price + order.fillMargin
          : bar.low <= tp.price - order.fillMargin;
      if (!hit) break;
      _exitAt(tp.price, tp.share);
      nextTp++;
    }
    if (nextTp >= order.takeProfits.length || remainingShare <= 1e-9) {
      return realizedR;
    }

    // Горизонт свинга вышел — закрываем остаток по рынку.
    if (index - openedAt >= maxHoldBars) {
      final exit = long ? bar.close - costs.slippage : bar.close + costs.slippage;
      _exitAt(exit, remainingShare, slippage: costs.slippage);
      return realizedR;
    }
    return null;
  }

  /// Закрытие доли [share] по цене [price]. [slippage] уже учтено в цене и
  /// передаётся только для учёта в [costR].
  void _exitAt(double price, double share, {double slippage = 0}) {
    final risk = order.risk;
    if (risk > 0) {
      final move = order.long ? price - order.entry : order.entry - price;
      realizedR += share * move / risk;
      if (slippage != 0) costR += share * slippage / risk;
    }
    _charge(share * order.costs.fee(price));
    remainingShare -= share;
    if (remainingShare < 1e-9) remainingShare = 0;
  }

  void _charge(double priceCost) {
    final risk = order.risk;
    if (risk <= 0 || priceCost == 0) return;
    final r = priceCost / risk;
    realizedR -= r;
    costR += r;
  }
}
