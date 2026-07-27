import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/ledger/money.dart';
import 'package:signalai/domain/portfolio/package_backtest.dart';
import 'package:signalai/domain/portfolio/package_plan.dart';
import 'package:signalai/domain/portfolio/rebalance.dart';

Money rub(num v) => Money.of(v, Currency.rub);

/// Ряд с постоянной дневной доходностью — результат известен заранее.
List<Candle> series({
  required double start,
  required double dailyReturn,
  int days = 500,
  DateTime? from,
}) {
  final result = <Candle>[];
  var price = start;
  var date = from ?? DateTime.utc(2020, 1, 1);
  for (var i = 0; i < days; i++) {
    result.add(Candle(
      time: date,
      open: price,
      high: price,
      low: price,
      close: price,
      volume: 1,
    ));
    price *= 1 + dailyReturn;
    date = date.add(const Duration(days: 1));
  }
  return result;
}

void main() {
  group('Замысел пакета', () {
    test('веса пакетов по умолчанию складываются в сто процентов', () {
      // Пакет, чьи веса не дают сотню, описывает не весь капитал — и остаток
      // молча зависает непонятно где.
      for (final plan in PackagePlan.defaults()) {
        expect(plan.totalWeight, closeTo(100, 1e-9), reason: plan.title);
      }
    });

    test('у каждого пакета есть правило инвалидации', () {
      // Без него пакет живёт вечно и превращается в кладбище решений.
      for (final plan in PackagePlan.defaults()) {
        expect(plan.invalidation, isNotEmpty, reason: plan.title);
      }
    });

    test('риск растёт от консервативного к рискованному', () {
      final plans = {for (final p in PackagePlan.defaults()) p.id: p};
      double risky(PackagePlan plan) => plan.targets
          .where((t) =>
              t.assetClass == AssetClass.stocks ||
              t.assetClass == AssetClass.crypto ||
              t.assetClass == AssetClass.futures)
          .fold(0.0, (sum, t) => sum + t.weightPercent);

      expect(risky(plans['conservative']!), lessThan(risky(plans['balanced']!)));
      expect(risky(plans['balanced']!), lessThan(risky(plans['aggressive']!)));
    });

    test('замысел переживает сериализацию', () {
      final plan = PackagePlan.defaults().first;
      final restored = PackagePlan.fromJson(plan.toJson());
      expect(restored.id, plan.id);
      expect(restored.targets.length, plan.targets.length);
      expect(restored.targets.first.bandPercent, plan.targets.first.bandPercent);
    });
  });

  group('Ребалансировка', () {
    final plan = PackagePlan(
      id: 'test',
      title: 'Тест',
      thesis: '',
      horizonYears: 5,
      invalidation: '',
      targets: const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 60, bandPercent: 5),
        PackageTarget(assetClass: AssetClass.bonds, weightPercent: 40, bandPercent: 5),
      ],
    );

    test('внутри полосы заявок нет', () {
      // Ребалансировка по любому отклонению съедает доходность комиссиями:
      // полоса и есть то, что отличает план от суеты.
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(620000), AssetClass.bonds: rub(380000)},
        total: rub(1000000),
      );
      expect(result.isEmpty, isTrue);
      expect(result.skipped.any((s) => s.contains('внутри полосы')), isTrue);
    });

    test('выход за полосу даёт заявки в обе стороны', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.bonds: rub(300000)},
        total: rub(1000000),
      );
      expect(result.orders.length, 2);

      final sell = result.orders.firstWhere((o) => !o.buy);
      final buy = result.orders.firstWhere((o) => o.buy);
      expect(sell.assetClass, AssetClass.stocks);
      expect(sell.amount, rub(100000));
      expect(buy.assetClass, AssetClass.bonds);
      expect(buy.amount, rub(100000));
      expect(sell.reason, contains('выше полосы'));
    });

    test('мелкая сделка не порождается', () {
      // Отклонение за полосой, но сделка меньше процента капитала: комиссия
      // и налог обойдутся дороже, чем даст выравнивание.
      final narrow = PackagePlan(
        id: 'narrow',
        title: '',
        thesis: '',
        horizonYears: 1,
        invalidation: '',
        targets: const [
          PackageTarget(assetClass: AssetClass.stocks, weightPercent: 60, bandPercent: 0.2),
          PackageTarget(assetClass: AssetClass.bonds, weightPercent: 40, bandPercent: 0.2),
        ],
      );
      final result = Rebalancer.plan(
        plan: narrow,
        values: {AssetClass.stocks: rub(605000), AssetClass.bonds: rub(395000)},
        total: rub(1000000),
      );
      expect(result.orders, isEmpty);
      expect(result.skipped.any((s) => s.contains('не окупает издержек')), isTrue);
    });

    test('количество считается в лотах и округляется вниз', () {
      // Заявка «на 143 217 ₽» неисполнима: округляем вниз, потому что недобор
      // остаётся деньгами, а перебор требует ещё одной сделки.
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.bonds: rub(300000)},
        total: rub(1000000),
        prices: {AssetClass.bonds: rub(1030), AssetClass.stocks: rub(280)},
      );
      final buy = result.orders.firstWhere((o) => o.buy);
      expect(buy.lots, 97); // 100000 / 1030 = 97,08
      expect(buy.amount, rub(97 * 1030));
    });

    test('не хватает на лот — заявки нет, а не заявка на ноль', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.bonds: rub(300000)},
        total: rub(1000000),
        prices: {AssetClass.bonds: rub(500000)},
      );
      expect(result.orders.any((o) => o.assetClass == AssetClass.bonds), isFalse);
      expect(result.skipped.any((s) => s.contains('один лот')), isTrue);
    });

    test('нулевой капитал объясняется, а не делится на ноль', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: const {},
        total: rub(0),
      );
      expect(result.isEmpty, isTrue);
      expect(result.skipped.single, contains('нулевой'));
    });

    test('фактические веса считаются от капитала', () {
      final positions = Rebalancer.positions(
        plan: plan,
        values: {AssetClass.stocks: rub(750000), AssetClass.bonds: rub(250000)},
        total: rub(1000000),
      );
      expect(positions.first.actualPercent, closeTo(75, 1e-9));
      expect(positions.first.drift, closeTo(15, 1e-9));
      expect(positions.first.outOfBand, isTrue);
    });
  });

  group('Историческая симуляция', () {
    final plan = PackagePlan(
      id: 'sim',
      title: 'Симуляция',
      thesis: '',
      horizonYears: 5,
      invalidation: '',
      targets: const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 50),
        PackageTarget(assetClass: AssetClass.bonds, weightPercent: 50),
      ],
    );

    test('на известном ряде считает известную доходность', () {
      // Оба класса растут на 0,04% в день ⇒ портфель растёт так же,
      // независимо от весов и ребалансировок.
      const daily = 0.0004;
      final result = PackageSimulator.run(
        plan: plan,
        series: {
          AssetClass.stocks: series(start: 100, dailyReturn: daily),
          AssetClass.bonds: series(start: 50, dailyReturn: daily),
        },
      );

      final expected = (math.pow(1 + daily, 365) - 1) * 100;
      expect(result.cagr, closeTo(expected, 0.5));
      expect(result.maxDrawdown, closeTo(0, 0.01), reason: 'растущий ряд без просадок');
      expect(result.equity.length, greaterThan(400));
    });

    test('просадка считается от пика, а не от начала', () {
      // Ряд растёт, потом падает вдвое: просадка 50%, а не результат периода.
      final up = series(start: 100, dailyReturn: 0.01, days: 100);
      final down = <Candle>[];
      var price = up.last.close;
      var date = up.last.time;
      for (var i = 0; i < 100; i++) {
        price *= 0.993;
        date = date.add(const Duration(days: 1));
        down.add(Candle(
          time: date,
          open: price,
          high: price,
          low: price,
          close: price,
          volume: 1,
        ));
      }
      final full = [...up, ...down];

      final single = PackagePlan(
        id: 'one',
        title: '',
        thesis: '',
        horizonYears: 1,
        invalidation: '',
        targets: const [
          PackageTarget(assetClass: AssetClass.stocks, weightPercent: 100),
        ],
      );
      final result = PackageSimulator.run(
        plan: single,
        series: {AssetClass.stocks: full},
      );
      expect(result.maxDrawdown, closeTo(-50.4, 1.5));
    });

    test('класс без истории называется, а не пропадает молча', () {
      final result = PackageSimulator.run(
        plan: plan,
        series: {AssetClass.stocks: series(start: 100, dailyReturn: 0.0003)},
      );
      expect(result.missing, contains('Облигации'));
      // Вес отсутствующего класса перераспределён, симуляция продолжается.
      expect(result.equity.length, greaterThan(400));
    });

    test('без единой серии возвращается пустой результат, а не ноль-прогноз', () {
      final result = PackageSimulator.run(plan: plan, series: const {});
      expect(result.isEmpty, isTrue);
      expect(result.missing.length, 2);
    });

    test('считается только по общим датам рядов', () {
      // Пропуск в одной серии не должен сдвигать остальные: иначе симуляция
      // считает портфель, которого не существовало.
      final long = series(start: 100, dailyReturn: 0.0003, days: 400);
      final short = series(
        start: 50,
        dailyReturn: 0.0003,
        days: 200,
        from: DateTime.utc(2020, 1, 1),
      );
      final result = PackageSimulator.run(
        plan: plan,
        series: {AssetClass.stocks: long, AssetClass.bonds: short},
      );
      expect(result.equity.length, 200);
    });
  });
}
