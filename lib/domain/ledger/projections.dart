import 'ledger_event.dart';
import 'money.dart';

/// Открытый лот: сколько куплено, по какой цене и когда.
///
/// Лоты нужны, потому что «средняя цена» теряет информацию: при частичной
/// продаже налоговый и торговый результат зависят от того, какие именно
/// бумаги проданы. Приложение считает FIFO — то же правило, что применяет
/// российский брокер, поэтому реализованный результат сходится с его отчётом.
class Lot {
  Lot({
    required this.eventId,
    required this.openedAt,
    required this.quantity,
    required this.price,
    required this.contour,
    this.packageId,
    this.tradeId,
  });

  final String eventId;
  final DateTime openedAt;

  /// Остаток лота: уменьшается по мере продаж.
  Quantity quantity;

  /// Цена входа за единицу (себестоимость).
  final Money price;

  final Contour? contour;
  final String? packageId;
  final String? tradeId;
}

/// Позиция как проекция книги: агрегат по инструменту и счёту.
class PositionProjection {
  PositionProjection({
    required this.accountId,
    required this.instrument,
    required this.quantity,
    required this.costBasis,
    required this.lots,
    this.contour,
  });

  final String accountId;
  final String instrument;

  /// Положительное — лонг, отрицательное — шорт.
  final Quantity quantity;

  /// Вложенные деньги: сумма остатков лотов по цене входа.
  final Money costBasis;

  final List<Lot> lots;
  final Contour? contour;

  bool get isLong => quantity.isPositive;
  bool get isEmpty => quantity.isZero;

  /// Средняя цена входа. Ноль штук — null, делить не на что.
  Money? get averagePrice {
    if (quantity.isZero) return null;
    final units = quantity.abs.asDouble;
    return Money(
      (costBasis.minor.abs() / units).round(),
      costBasis.currency,
    );
  }

  /// Нереализованный результат по текущей цене.
  Money unrealized(Money last) {
    final value = last.multiplyBy(quantity.abs);
    return isLong ? value - costBasis.abs : costBasis.abs - value;
  }
}

/// Разложение результата: из чего он сложился.
class PnlBreakdown {
  const PnlBreakdown({
    required this.realized,
    required this.income,
    required this.fees,
    required this.taxes,
    required this.currency,
  });

  /// Результат закрытых сделок до издержек.
  final Money realized;

  /// Дивиденды, купоны, проценты, положительный фандинг.
  final Money income;

  /// Комиссии и отрицательный фандинг (значение положительное).
  final Money fees;

  /// Налоги (значение положительное).
  final Money taxes;

  final Currency currency;

  /// Чистый результат: то, что реально осталось.
  Money get net => realized + income - fees - taxes;

  static PnlBreakdown empty(Currency currency) => PnlBreakdown(
        realized: Money.zero(currency),
        income: Money.zero(currency),
        fees: Money.zero(currency),
        taxes: Money.zero(currency),
        currency: currency,
      );
}

/// Состояние капитала, посчитанное из книги.
class LedgerSnapshot {
  const LedgerSnapshot({
    required this.at,
    required this.cash,
    required this.positions,
    required this.pnl,
    required this.contributed,
    required this.withdrawn,
    required this.base,
    required this.eventCount,
    required this.mismatches,
  });

  final DateTime at;

  /// Деньги по счетам и валютам: `accountId → валюта → сумма`.
  final Map<String, Map<String, Money>> cash;

  /// Открытые позиции.
  final List<PositionProjection> positions;

  /// Реализованный результат и его состав.
  final PnlBreakdown pnl;

  /// Сколько владелец внёс всего (пополнения и начальные остатки).
  final Money contributed;

  /// Сколько вывел.
  final Money withdrawn;

  final Currency base;

  final int eventCount;

  /// Записи с расхождением: их видно на «Сегодня», а не в настройках.
  final int mismatches;

  /// Чистый внесённый капитал: внесено минус выведено.
  Money get netContributed => contributed - withdrawn;

  /// Все деньги по всем счетам в базовой валюте.
  ///
  /// Валюты, отличные от базовой, требуют курса — они складываются отдельно
  /// и показываются как есть, а не пересчитываются молча по выдуманному
  /// курсу.
  Money cashInBase() {
    var total = Money.zero(base);
    for (final byCurrency in cash.values) {
      final own = byCurrency[base.code];
      if (own != null) total += own;
    }
    return total;
  }

  /// Деньги в валютах, отличных от базовой: `код → сумма`.
  Map<String, Money> foreignCash() {
    final result = <String, Money>{};
    for (final byCurrency in cash.values) {
      for (final entry in byCurrency.entries) {
        if (entry.key == base.code) continue;
        final existing = result[entry.key];
        result[entry.key] = existing == null ? entry.value : existing + entry.value;
      }
    }
    return result;
  }
}

/// Детерминированный расчёт проекций из книги.
///
/// «Детерминированный» здесь буквально: один и тот же список событий всегда
/// даёт одинаковый результат, порядок применения задан временем операции, а
/// не порядком записи в файл. Именно поэтому запоздавший импорт брокера не
/// портит историю — событие встаёт на своё место по [LedgerEvent.effectiveAt].
class LedgerProjector {
  const LedgerProjector({this.base = Currency.rub});

  final Currency base;

  /// Считает состояние на момент [asOf] (по умолчанию — по всем событиям).
  LedgerSnapshot project(List<LedgerEvent> events, {DateTime? asOf}) {
    final ordered = sortEvents(events, asOf: asOf);

    final cash = <String, Map<String, Money>>{};
    final lots = <String, List<Lot>>{}; // ключ: счёт|инструмент
    final shorts = <String, List<Lot>>{};
    var realized = Money.zero(base);
    var income = Money.zero(base);
    var fees = Money.zero(base);
    var taxes = Money.zero(base);
    var contributed = Money.zero(base);
    var withdrawn = Money.zero(base);
    var mismatches = 0;
    final contours = <String, Contour?>{};

    void addCash(String account, Money amount) {
      final byCurrency = cash.putIfAbsent(account, () => <String, Money>{});
      final existing = byCurrency[amount.currency.code];
      byCurrency[amount.currency.code] =
          existing == null ? amount : existing + amount;
    }

    /// Суммирует в базовой валюте: чужая валюта пересчитывается сохранённым
    /// курсом операции. Курса нет — вклад не учитывается, и это видно по
    /// расхождению, а не молча искажает итог.
    Money inBase(Money value, double? rate) {
      if (value.currency == base) return value;
      if (rate == null) return Money.zero(base);
      return value.convert(base, rate);
    }

    for (final event in ordered) {
      if (event.reconcile == ReconcileStatus.mismatch) mismatches++;

      final key = '${event.accountId}|${event.instrument ?? ''}';
      if (event.instrument != null && event.contour != null) {
        contours[key] = event.contour;
      }

      switch (event.type) {
        case LedgerEventType.opening:
        case LedgerEventType.deposit:
          addCash(event.accountId, event.cashImpact);
          contributed += inBase(event.cashImpact, event.fxRate);
        case LedgerEventType.withdrawal:
          addCash(event.accountId, event.cashImpact);
          withdrawn += inBase(event.cashImpact.abs, event.fxRate);
        case LedgerEventType.transfer:
        case LedgerEventType.exchange:
          addCash(event.accountId, event.cashImpact);
          if (event.counterAccountId != null) {
            // Вторая нога перевода записывается отдельным событием на
            // встречном счёте; здесь только своя сторона, иначе перевод
            // удвоится.
          }
        case LedgerEventType.buy:
          addCash(event.accountId, event.cashImpact);
          realized += _closeAgainst(
            shorts.putIfAbsent(key, () => []),
            event,
            base,
            long: false,
            onOpen: (lot) => lots.putIfAbsent(key, () => []).add(lot),
          );
        case LedgerEventType.sell:
          addCash(event.accountId, event.cashImpact);
          realized += _closeAgainst(
            lots.putIfAbsent(key, () => []),
            event,
            base,
            long: true,
            onOpen: (lot) => shorts.putIfAbsent(key, () => []).add(lot),
          );
        case LedgerEventType.fee:
          addCash(event.accountId, event.cashImpact);
          fees += inBase(event.cashImpact.abs, event.fxRate);
        case LedgerEventType.funding:
          addCash(event.accountId, event.cashImpact);
          // Фандинг ходит в обе стороны: платёж — издержка, получение — доход.
          if (event.cashImpact.isNegative) {
            fees += inBase(event.cashImpact.abs, event.fxRate);
          } else {
            income += inBase(event.cashImpact, event.fxRate);
          }
        case LedgerEventType.variationMargin:
          addCash(event.accountId, event.cashImpact);
          if (event.cashImpact.isNegative) {
            realized -= inBase(event.cashImpact.abs, event.fxRate);
          } else {
            realized += inBase(event.cashImpact, event.fxRate);
          }
        case LedgerEventType.dividend:
        case LedgerEventType.coupon:
        case LedgerEventType.interest:
          addCash(event.accountId, event.cashImpact);
          income += inBase(event.cashImpact, event.fxRate);
        case LedgerEventType.tax:
          addCash(event.accountId, event.cashImpact);
          taxes += inBase(event.cashImpact.abs, event.fxRate);
        case LedgerEventType.correction:
          addCash(event.accountId, event.cashImpact);
          // Корректировка меняет деньги и, если сказано, результат — но
          // исходную запись не трогает: обе видны в книге.
          if (event.affectsPnl) {
            realized += inBase(event.cashImpact, event.fxRate);
          }
      }

      // Комиссия и налог, удержанные отдельными полями сделки.
      final fee = event.fee;
      if (fee != null && !fee.isZero && event.type != LedgerEventType.fee) {
        fees += inBase(fee.abs, event.fxRate);
      }
      final tax = event.tax;
      if (tax != null && !tax.isZero && event.type != LedgerEventType.tax) {
        taxes += inBase(tax.abs, event.fxRate);
      }
    }

    final positions = <PositionProjection>[];
    for (final entry in {...lots.keys, ...shorts.keys}) {
      final long = lots[entry] ?? const <Lot>[];
      final short = shorts[entry] ?? const <Lot>[];
      final open = [...long, ...short].where((l) => !l.quantity.isZero).toList();
      if (open.isEmpty) continue;

      final parts = entry.split('|');
      final isShort = long.every((l) => l.quantity.isZero);
      var quantity = Quantity.zero;
      var cost = Money.zero(open.first.price.currency);
      for (final lot in open) {
        quantity += lot.quantity;
        cost += lot.price.multiplyBy(lot.quantity);
      }
      positions.add(PositionProjection(
        accountId: parts[0],
        instrument: parts[1],
        quantity: isShort ? -quantity : quantity,
        costBasis: cost,
        lots: open,
        contour: contours[entry],
      ));
    }

    return LedgerSnapshot(
      at: asOf ?? DateTime.now().toUtc(),
      cash: cash,
      positions: positions,
      pnl: PnlBreakdown(
        realized: realized,
        income: income,
        fees: fees,
        taxes: taxes,
        currency: base,
      ),
      contributed: contributed,
      withdrawn: withdrawn,
      base: base,
      eventCount: ordered.length,
      mismatches: mismatches,
    );
  }

  /// Порядок применения: по времени операции, при равенстве — по id.
  ///
  /// Сортировка по id при совпадении времени нужна, чтобы результат не
  /// зависел от порядка импорта: два исполнения в одну миллисекунду должны
  /// давать один и тот же расчёт при любом порядке загрузки.
  static List<LedgerEvent> sortEvents(List<LedgerEvent> events, {DateTime? asOf}) {
    final list = [
      for (final e in events)
        if (asOf == null || !e.effectiveAt.isAfter(asOf)) e,
    ]..sort((a, b) {
        final byTime = a.effectiveAt.compareTo(b.effectiveAt);
        return byTime != 0 ? byTime : a.id.compareTo(b.id);
      });
    return list;
  }
}

/// Закрывает встречные лоты по FIFO и возвращает реализованный результат.
///
/// [long] = true — закрываем длинные лоты продажей; false — короткие покупкой.
/// Остаток, который нечем закрыть, открывает лот в обратную сторону: так
/// разворот позиции одной сделкой считается правильно, а не теряется.
Money _closeAgainst(
  List<Lot> lots,
  LedgerEvent event,
  Currency base, {
  required bool long,
  required void Function(Lot) onOpen,
}) {
  var remaining = event.quantity ?? Quantity.zero;
  final price = event.price;
  if (price == null || remaining.isZero) return Money.zero(base);

  var realized = Money.zero(base);
  for (final lot in lots) {
    if (remaining.isZero) break;
    if (lot.quantity.isZero) continue;
    final take = lot.quantity.min(remaining);
    final proceeds = price.multiplyBy(take);
    final cost = lot.price.multiplyBy(take);
    // Лонг зарабатывает разницей «продал минус купил», шорт — наоборот.
    final result = long ? proceeds - cost : cost - proceeds;
    realized += result.currency == base ? result : result.convert(base, event.fxRate ?? 1);
    lot.quantity -= take;
    remaining -= take;
  }
  lots.removeWhere((l) => l.quantity.isZero);

  if (!remaining.isZero) {
    onOpen(Lot(
      eventId: event.id,
      openedAt: event.effectiveAt,
      quantity: remaining,
      price: price,
      contour: event.contour,
      packageId: event.packageId,
      tradeId: event.tradeId,
    ));
  }
  return realized;
}
