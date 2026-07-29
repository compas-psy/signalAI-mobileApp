import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/ledger/money.dart';
import 'package:signalai/domain/portfolio/allocation.dart';
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
              t.assetClass == AssetClass.dividendStocks)
          .fold(0.0, (sum, t) => sum + t.weightPercent);

      expect(risky(plans['conservative']!), lessThan(risky(plans['balanced']!)));
      expect(risky(plans['balanced']!), lessThan(risky(plans['aggressive']!)));
    });

    test('фьючерса как класса активов нет', () {
      // Фьючерс на индекс даёт ту же экспозицию, что и фонд акций: 65% акций
      // плюс 10% фьючерса — это 75% беты одного индекса, подписанные как
      // диверсификация. Плечо и хедж — инструмент тактики, а не доля пакета.
      for (final assetClass in AssetClass.values) {
        expect(assetClass.name, isNot('futures'));
      }
    });

    test('облигации разложены на разные источники риска', () {
      // Один класс «облигации» склеивал процентный риск, кредитный и почти
      // нулевую дюрацию. В цикле роста ставки такая «защита» даёт акционерную
      // просадку.
      expect(AssetClass.values, contains(AssetClass.ofz));
      expect(AssetClass.values, contains(AssetClass.corpBonds));
      expect(AssetClass.values, contains(AssetClass.floaters));
    });

    test('во всех пакетах есть нога, не зависящая от рублёвой ставки', () {
      // Рублёвый портфель без товарной и валютной ноги полностью открыт
      // девальвации, и ни одна его часть от неё не защищает.
      for (final plan in PackagePlan.defaults()) {
        final hedges = plan.targets.where((t) =>
            t.assetClass == AssetClass.gold ||
            t.assetClass == AssetClass.fxBonds);
        expect(hedges, isNotEmpty, reason: plan.title);
      }
    });

    test('полоса считается от веса, а не константой', () {
      // ±5 п.п. при цели 10% позволяют классу уполовиниться, не выйдя из
      // полосы; те же ±5 при цели 55% заставляют торговать из-за косметики.
      expect(PackageTarget.defaultBand(10), 3.0, reason: 'нижняя отсечка');
      expect(PackageTarget.defaultBand(40), 10.0.clamp(3.0, 8.0));
      expect(PackageTarget.defaultBand(20), 5.0);
      expect(PackageTarget.defaultBand(4), 3.0);
    });

    test('у каждого класса написано, зачем он в пакете', () {
      // Без этого владелец не может решить, какой класс резать первым.
      for (final assetClass in AssetClass.values) {
        expect(assetClass.thesis, isNotEmpty, reason: assetClass.label);
      }
    });

    test('класс без индекса честно помечен', () {
      // Флоатеры и валютные облигации симулировать нечем: индекса полной
      // доходности у биржи для них нет. Подставить похожий ряд нельзя.
      expect(AssetClass.floaters.hasBenchmark, isFalse);
      expect(AssetClass.fxBonds.hasBenchmark, isFalse);
      expect(AssetClass.gold.hasBenchmark, isTrue);
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
        PackageTarget(assetClass: AssetClass.ofz, weightPercent: 40, bandPercent: 5),
      ],
    );

    test('внутри полосы заявок нет', () {
      // Ребалансировка по любому отклонению съедает доходность комиссиями:
      // полоса и есть то, что отличает план от суеты.
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(620000), AssetClass.ofz: rub(380000)},
        total: rub(1000000),
      );
      expect(result.isEmpty, isTrue);
      expect(result.skipped.any((s) => s.contains('внутри полосы')), isTrue);
    });

    test('выход за полосу даёт заявки в обе стороны', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
      );
      expect(result.orders.length, 2);

      final sell = result.orders.firstWhere((o) => !o.buy);
      final buy = result.orders.firstWhere((o) => o.buy);
      expect(sell.assetClass, AssetClass.stocks);
      expect(sell.amount, rub(100000));
      expect(buy.assetClass, AssetClass.ofz);
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
          PackageTarget(assetClass: AssetClass.ofz, weightPercent: 40, bandPercent: 0.2),
        ],
      );
      final result = Rebalancer.plan(
        plan: narrow,
        values: {AssetClass.stocks: rub(605000), AssetClass.ofz: rub(395000)},
        total: rub(1000000),
      );
      expect(result.orders, isEmpty);
      expect(result.skipped.any((s) => s.contains('не окупает издержек')), isTrue);
    });

    test('пополнение оплачивает докупку и помечает её (ТЗ §7.2)', () {
      // Продажа фиксирует прибыль и налог с неё. Докупка на свежие деньги
      // выравнивает тот же вес бесплатно — поэтому ТЗ требует сначала
      // потратить пополнение и только потом предлагать продажи.
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
        contribution: rub(150000),
      );
      final buy = result.buys.single;
      expect(buy.assetClass, AssetClass.ofz);
      expect(buy.fromContribution, isTrue);
      expect(buy.reason, contains('оплачиваем пополнением'));
    });

    test('пополнения не хватило — покупка не притворяется бесплатной', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
        contribution: rub(10000),
      );
      expect(result.buys.single.fromContribution, isFalse);
    });

    test('без пополнения ничего не помечается оплаченным', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
      );
      expect(result.orders.every((o) => !o.fromContribution), isTrue);
    });

    test('покупки идут раньше продаж', () {
      // Порядок в списке — это порядок исполнения: сначала то, что не
      // требует ничего продавать.
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
        contribution: rub(150000),
      );
      expect(result.orders.first.buy, isTrue);
      expect(result.orders.last.buy, isFalse);
      expect(result.buys.length, 1);
      expect(result.sells.length, 1);
    });

    test('количество считается в лотах и округляется вниз', () {
      // Заявка «на 143 217 ₽» неисполнима: округляем вниз, потому что недобор
      // остаётся деньгами, а перебор требует ещё одной сделки.
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
        prices: {AssetClass.ofz: rub(1030), AssetClass.stocks: rub(280)},
      );
      final buy = result.orders.firstWhere((o) => o.buy);
      expect(buy.lots, 97); // 100000 / 1030 = 97,08
      expect(buy.amount, rub(97 * 1030));
    });

    test('не хватает на лот — заявки нет, а не заявка на ноль', () {
      final result = Rebalancer.plan(
        plan: plan,
        values: {AssetClass.stocks: rub(700000), AssetClass.ofz: rub(300000)},
        total: rub(1000000),
        prices: {AssetClass.ofz: rub(500000)},
      );
      expect(result.orders.any((o) => o.assetClass == AssetClass.ofz), isFalse);
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
        values: {AssetClass.stocks: rub(750000), AssetClass.ofz: rub(250000)},
        total: rub(1000000),
      );
      expect(positions.first.actualPercent, closeTo(75, 1e-9));
      expect(positions.first.drift, closeTo(15, 1e-9));
      expect(positions.first.outOfBand, isTrue);
    });
  });

  group('Разбор пакета до инструментов', () {
    final plan = PackagePlan(
      id: 'alloc',
      title: 'Разбор',
      thesis: '',
      horizonYears: 5,
      invalidation: '',
      targets: const [
        PackageTarget(assetClass: AssetClass.stocks, weightPercent: 60),
        PackageTarget(assetClass: AssetClass.ofz, weightPercent: 40),
      ],
    );

    test('у каждого класса есть исполнимый инструмент', () {
      // «30% в облигациях» без указания, чем именно, — пожелание, а не план:
      // в терминал такое не выставляется.
      for (final assetClass in AssetClass.values) {
        expect(assetClass.proxy, isNotEmpty, reason: assetClass.label);
      }
    });

    test('целевые суммы превращаются в лоты и штуки', () {
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(1000000),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(7.5), lotSize: 10),
          'SBGB': InstrumentQuote(symbol: 'SBGB', price: rub(11.2), lotSize: 1),
        },
      );

      final stocks =
          allocation.lines.firstWhere((l) => l.assetClass == AssetClass.stocks);
      // 600 000 / (7,5 × 10) = 8000 лотов ⇒ 80 000 паёв.
      expect(stocks.lots, 8000);
      expect(stocks.units, 80000);
      expect(stocks.plannedValue, rub(600000));

      final bonds =
          allocation.lines.firstWhere((l) => l.assetClass == AssetClass.ofz);
      // 400 000 / 11,2 = 35 714,28 ⇒ 35 714 штук, округление вниз.
      expect(bonds.lots, 35714);
      expect(bonds.plannedValue, rub(35714 * 11.2));
    });

    test('остаток, не легший в целые лоты, назван, а не потерян', () {
      // Если остаток «размазать», сумма строк не сойдётся с капиталом — и
      // доверие к разбору кончится на первом же сложении.
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(1000000),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(7.5), lotSize: 10),
          'SBGB': InstrumentQuote(symbol: 'SBGB', price: rub(11.2), lotSize: 1),
        },
      );

      expect(allocation.planned + allocation.residual, allocation.total);
      expect(allocation.residual.isNegative, isFalse);
    });

    test('нулевой капитал — одна причина на пакет, а не на каждую строку', () {
      // На экране было три одинаковых предупреждения подряд и ни одного
      // числа: нулевой капитал — свойство пакета, а не инструмента в нём.
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(0),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(7.5), lotSize: 10),
          'SBGB': InstrumentQuote(symbol: 'SBGB', price: rub(11.2)),
        },
      );

      expect(allocation.blocker, isNotNull);
      expect(allocation.blocker, contains('Капитала'));
      // Состав всё равно показывается: веса и инструменты известны без денег.
      expect(allocation.lines.length, 2);
      expect(allocation.lines.every((l) => l.reason == null), isTrue);
      expect(allocation.lines.first.quote, isNotNull);
    });

    test('без цены количество не выдумывается', () {
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(1000000),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(7.5), lotSize: 10),
        },
      );

      final bonds =
          allocation.lines.firstWhere((l) => l.assetClass == AssetClass.ofz);
      expect(bonds.lots, isNull);
      expect(bonds.reason, contains('цены'));
      expect(allocation.unpriced.length, 1);
    });

    test('чужая валюта требует курса, а не молчаливого сложения', () {
      // Сложить рубли с USDT «как есть» — тот же дефект, что и double
      // для денег: ошибка не видна, пока не станет дорогой.
      final crypto = PackagePlan(
        id: 'crypto',
        title: '',
        thesis: '',
        horizonYears: 1,
        invalidation: '',
        targets: const [
          PackageTarget(assetClass: AssetClass.crypto, weightPercent: 100),
        ],
      );
      final allocation = TargetAllocation.of(
        plan: crypto,
        total: rub(1000000),
        quotes: {
          'BTCUSDT': InstrumentQuote(
            symbol: 'BTCUSDT',
            price: Money.of(90000, Currency.usdt),
            venue: 'Bybit',
          ),
        },
      );

      expect(allocation.lines.single.lots, isNull);
      expect(allocation.lines.single.reason, contains('курс'));
    });

    test('неполный лот не превращается в заявку на ноль', () {
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(1000),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(700), lotSize: 10),
        },
      );

      final stocks =
          allocation.lines.firstWhere((l) => l.assetClass == AssetClass.stocks);
      expect(stocks.lots, isNull);
      expect(stocks.reason, contains('один лот'));
    });

    test('уже купленное вычитается: докупить надо разницу', () {
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(1000000),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(7.5), lotSize: 10),
        },
        holdings: const {'EQMX': 30000},
      );

      final stocks =
          allocation.lines.firstWhere((l) => l.assetClass == AssetClass.stocks);
      expect(stocks.actualUnits, 30000);
      expect(stocks.deltaUnits, 50000);
      expect(stocks.deltaValue, rub(50000 * 7.5));
      expect(stocks.buy, isTrue);
    });

    test('перебор даёт продажу, а не отрицательную покупку', () {
      final allocation = TargetAllocation.of(
        plan: plan,
        total: rub(1000000),
        quotes: {
          'EQMX': InstrumentQuote(symbol: 'EQMX', price: rub(7.5), lotSize: 10),
        },
        holdings: const {'EQMX': 100000},
      );

      final stocks =
          allocation.lines.firstWhere((l) => l.assetClass == AssetClass.stocks);
      expect(stocks.deltaUnits, -20000);
      expect(stocks.buy, isFalse);
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
        PackageTarget(assetClass: AssetClass.ofz, weightPercent: 50),
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
          AssetClass.ofz: series(start: 50, dailyReturn: daily),
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
      expect(result.missing, contains('ОФЗ'));
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
        series: {AssetClass.stocks: long, AssetClass.ofz: short},
      );
      expect(result.equity.length, 200);
    });
  });
}
