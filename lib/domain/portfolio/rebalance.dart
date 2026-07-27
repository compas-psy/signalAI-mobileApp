import '../ledger/money.dart';
import 'package_plan.dart';

/// Одна заявка предложения по ребалансировке.
class RebalanceOrder {
  const RebalanceOrder({
    required this.assetClass,
    required this.buy,
    required this.amount,
    required this.reason,
    this.instrument,
    this.lots,
    this.lotPrice,
  });

  final AssetClass assetClass;

  /// Покупаем или продаём.
  final bool buy;

  /// Сумма в базовой валюте.
  final Money amount;

  /// Почему эта заявка нужна.
  final String reason;

  /// Инструмент-прокси, если он определён.
  final String? instrument;

  /// Количество лотов — заявка должна быть исполнимой, а не «на 43 217 ₽».
  final int? lots;

  final Money? lotPrice;
}

/// Предложение по ребалансировке пакета.
class RebalancePlan {
  const RebalancePlan({
    required this.orders,
    required this.positions,
    required this.total,
    required this.skipped,
  });

  final List<RebalanceOrder> orders;
  final List<ClassPosition> positions;
  final Money total;

  /// Классы, которые сознательно не трогали, и почему.
  final List<String> skipped;

  bool get isEmpty => orders.isEmpty;

  /// Сумма покупок и продаж — оборот ребалансировки.
  Money get turnover {
    var sum = Money.zero(total.currency);
    for (final order in orders) {
      sum += order.amount.abs;
    }
    return sum;
  }
}

/// Расчёт ребалансировки пакета.
///
/// Профессиональная ребалансировка отличается от арифметической двумя
/// вещами: полосой допуска и порогом минимальной сделки. Без них портфель
/// торгуется каждый день по чуть-чуть, и комиссия с налогом съедают ровно
/// ту доходность, ради которой ребалансировка затевалась.
abstract final class Rebalancer {
  /// Минимальная сделка в процентах капитала пакета.
  ///
  /// Сделка меньше этого порога не окупает издержек: комиссия, спред и налог
  /// с прибыли обойдутся дороже, чем даст выравнивание веса.
  static const minimumTradePercent = 1.0;

  /// Считает фактические веса классов.
  static List<ClassPosition> positions({
    required PackagePlan plan,
    required Map<AssetClass, Money> values,
    required Money total,
  }) {
    return [
      for (final target in plan.targets)
        ClassPosition(
          assetClass: target.assetClass,
          value: values[target.assetClass] ?? Money.zero(total.currency),
          actualPercent: total.isZero
              ? 0
              : (values[target.assetClass] ?? Money.zero(total.currency)).minor /
                  total.minor *
                  100,
          target: target,
        ),
    ];
  }

  /// Предложение по ребалансировке.
  ///
  /// [prices] — цена лота инструмента-прокси класса, если известна: тогда
  /// заявка считается в лотах, а не «на сумму». Заявка, которую нельзя
  /// выставить как есть, бесполезна.
  static RebalancePlan plan({
    required PackagePlan plan,
    required Map<AssetClass, Money> values,
    required Money total,
    Map<AssetClass, Money> prices = const {},
  }) {
    final positions = Rebalancer.positions(plan: plan, values: values, total: total);
    final orders = <RebalanceOrder>[];
    final skipped = <String>[];

    if (total.isZero) {
      return RebalancePlan(
        orders: const [],
        positions: positions,
        total: total,
        skipped: const ['Капитал пакета нулевой — ребалансировать нечего'],
      );
    }

    for (final position in positions) {
      final target = position.target;
      if (!position.outOfBand) {
        skipped.add('${position.assetClass.label}: '
            '${_pp(position.drift)} — внутри полосы ±${target.bandPercent.round()} п.п.');
        continue;
      }

      final targetValue = total.scaleBy(target.weightPercent / 100);
      final delta = targetValue - position.value;
      final sizePercent = delta.minor.abs() / total.minor * 100;

      if (sizePercent < minimumTradePercent) {
        skipped.add('${position.assetClass.label}: сделка на '
            '${sizePercent.toStringAsFixed(1)}% капитала не окупает издержек');
        continue;
      }

      final price = prices[position.assetClass];
      int? lots;
      if (price != null && !price.isZero) {
        // Округляем вниз: перебрать вес хуже, чем недобрать — недобор
        // остаётся деньгами, перебор требует ещё одной сделки.
        lots = (delta.minor.abs() / price.minor).floor();
        if (lots == 0) {
          skipped.add('${position.assetClass.label}: не хватает на один лот '
              '${position.assetClass.examples.first}');
          continue;
        }
      }

      orders.add(RebalanceOrder(
        assetClass: position.assetClass,
        buy: delta.isPositive,
        amount: lots == null ? delta.abs : price!.scaleBy(lots.toDouble()),
        instrument: position.assetClass.examples.first,
        lots: lots,
        lotPrice: price,
        reason: delta.isPositive
            ? 'вес ${_pct(position.actualPercent)} ниже полосы '
                '${_pct(target.lowerBound)} — докупаем до цели '
                '${_pct(target.weightPercent)}'
            : 'вес ${_pct(position.actualPercent)} выше полосы '
                '${_pct(target.upperBound)} — сокращаем до цели '
                '${_pct(target.weightPercent)}',
      ));
    }

    return RebalancePlan(
      orders: orders,
      positions: positions,
      total: total,
      skipped: skipped,
    );
  }

  static String _pct(double value) =>
      '${value.toStringAsFixed(value.abs() < 10 ? 1 : 0).replaceAll('.', ',')}%';

  static String _pp(double value) =>
      '${value >= 0 ? '+' : '−'}${value.abs().toStringAsFixed(1).replaceAll('.', ',')} п.п.';
}
