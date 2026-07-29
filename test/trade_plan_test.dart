import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/trade_plan.dart';

/// Здоровый план: лонг 100→96, цели 108/112/120 с долями 40/40/20.
TradePlan plan({
  Direction direction = Direction.long,
  double entryLow = 99,
  double entryHigh = 101,
  double stop = 96,
  List<PlanTarget>? targets,
  int quantity = 10,
  int lotSize = 1,
  double riskRubles = 4000,
  double riskPercent = 0.75,
  DateTime? expiry,
}) =>
    TradePlan(
      instrumentId: 'SiZ6',
      direction: direction,
      orderType: PlanOrderType.limit,
      entryLow: entryLow,
      entryHigh: entryHigh,
      maxSlippagePercent: 0.15,
      stop: stop,
      targets: targets ??
          const [
            PlanTarget(name: 'TP1', price: 108, fraction: 0.4, afterFill: 'стоп в БУ'),
            PlanTarget(name: 'TP2', price: 112, fraction: 0.4, afterFill: 'трейлинг 1H'),
            PlanTarget(name: 'TP3', price: 120, fraction: 0.2, afterFill: 'разбор'),
          ],
      quantity: quantity,
      lotSize: lotSize,
      valuePerPoint: 100,
      riskRubles: riskRubles,
      riskPercent: riskPercent,
      marginEstimate: 25000,
      expiry: expiry ?? DateTime.utc(2026, 7, 30, 18),
      strategyVersion: 'trend-pullback-1.0.0',
    );

void main() {
  group('Арифметика плана', () {
    test('вход — середина зоны, риск — расстояние до стопа', () {
      final p = plan();
      expect(p.entry, 100);
      expect(p.priceRisk, 4);
      expect(p.riskPerUnit, 400);
    });

    test('R/R считается по направлению сделки', () {
      final long = plan();
      expect(long.rrTo(long.targets[0]), closeTo(2.0, 1e-9));
      expect(long.rrToSecondTarget, closeTo(3.0, 1e-9));

      // В шорте прибыль внизу: знак обязан развернуться, иначе движок
      // посчитает убыточную цель прибыльной.
      final short = plan(
        direction: Direction.short,
        stop: 104,
        targets: const [
          PlanTarget(name: 'TP1', price: 92, fraction: 0.5, afterFill: ''),
          PlanTarget(name: 'TP2', price: 88, fraction: 0.5, afterFill: ''),
        ],
      );
      expect(short.rrTo(short.targets[0]), closeTo(2.0, 1e-9));
      expect(short.rrToSecondTarget, closeTo(3.0, 1e-9));
    });

    test('взвешенный результат учитывает доли закрытия', () {
      // 0,4×2R + 0,4×3R + 0,2×5R = 3,0R. Считать по TP3 целиком — враньё:
      // до последней цели доезжает пятая часть позиции.
      expect(plan().weightedR, closeTo(3.0, 1e-9));
    });

    test('срок плана истекает по времени, а не по желанию', () {
      final p = plan(expiry: DateTime.utc(2026, 7, 30, 18));
      expect(p.isExpired(DateTime.utc(2026, 7, 30, 17, 59)), isFalse);
      expect(p.isExpired(DateTime.utc(2026, 7, 30, 18)), isTrue);
    });
  });

  group('Структурные запреты (ТЗ §11.1, §13.1)', () {
    test('здоровый план не имеет претензий', () {
      expect(plan().structuralBlockers(), isEmpty);
    });

    test('стоп по неверную сторону входа блокирует план', () {
      // Стоп выше входа в лонге — не защита, а гарантированный убыток.
      expect(plan(stop: 102).structuralBlockers(),
          contains('в лонге стоп должен быть ниже зоны входа'));

      final short = plan(
        direction: Direction.short,
        stop: 96,
        targets: const [
          PlanTarget(name: 'TP1', price: 92, fraction: 0.5, afterFill: ''),
          PlanTarget(name: 'TP2', price: 88, fraction: 0.5, afterFill: ''),
        ],
      );
      expect(short.structuralBlockers(),
          contains('в шорте стоп должен быть выше зоны входа'));
    });

    test('план без стопа невозможен: риск не определён', () {
      // ТЗ §13.1: нельзя подтвердить план без SL и полного sizing.
      expect(plan(stop: 100).structuralBlockers(),
          contains('стоп совпадает с ценой входа — риск не определён'));
    });

    test('доли закрытия обязаны давать сто процентов', () {
      final broken = plan(targets: const [
        PlanTarget(name: 'TP1', price: 108, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP2', price: 112, fraction: 0.4, afterFill: ''),
      ]);
      expect(broken.structuralBlockers(),
          contains('доли закрытия дают 80% вместо 100%'));
    });

    test('цели идут по возрастанию расстояния', () {
      final tangled = plan(targets: const [
        PlanTarget(name: 'TP1', price: 112, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP2', price: 108, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP3', price: 120, fraction: 0.2, afterFill: ''),
      ]);
      expect(tangled.structuralBlockers(), contains('TP2 не дальше TP1'));
    });

    test('цель против направления сделки не проходит', () {
      final wrong = plan(targets: const [
        PlanTarget(name: 'TP1', price: 95, fraction: 1.0, afterFill: ''),
      ]);
      expect(wrong.structuralBlockers(),
          contains('TP1 стоит не по направлению сделки'));
    });

    test('R/R ниже порога исполнения блокирует план', () {
      // ТЗ §20: execution floor 1:1,8. Ниже — сделка не окупает издержек.
      final thin = plan(targets: const [
        PlanTarget(name: 'TP1', price: 104, fraction: 0.5, afterFill: ''),
        PlanTarget(name: 'TP2', price: 106, fraction: 0.5, afterFill: ''),
      ]);
      expect(thin.rrToSecondTarget, closeTo(1.5, 1e-9));
      expect(thin.structuralBlockers().join(), contains('ниже порога'));
    });

    test('объём меньше лота оставляет идею информационной', () {
      // ТЗ §20.1: quantity < min_lot → идея не подтверждается.
      final small = plan(quantity: 0);
      expect(small.structuralBlockers(),
          contains('объём меньше лота — идея остаётся информационной'));
    });

    test('объём не кратный лоту не проходит', () {
      expect(plan(quantity: 7, lotSize: 5).structuralBlockers(),
          contains('объём не кратен лоту'));
    });
  });

  group('Отпечаток плана (ТЗ §11.1, §17.1)', () {
    test('одинаковые планы дают одинаковый отпечаток', () {
      expect(plan().hash, plan().hash);
      expect(plan().hash.length, 8);
    });

    test('любое изменение цены, объёма или срока меняет отпечаток', () {
      // Иначе финальная проверка пропустит подмену: показали одно,
      // подтвердили другое.
      final base = plan().hash;
      expect(plan(stop: 95).hash, isNot(base));
      expect(plan(entryLow: 98).hash, isNot(base));
      expect(plan(quantity: 11).hash, isNot(base));
      expect(plan(expiry: DateTime.utc(2026, 7, 30, 19)).hash, isNot(base));
      expect(
        plan(targets: const [
          PlanTarget(name: 'TP1', price: 109, fraction: 0.4, afterFill: ''),
          PlanTarget(name: 'TP2', price: 112, fraction: 0.4, afterFill: ''),
          PlanTarget(name: 'TP3', price: 120, fraction: 0.2, afterFill: ''),
        ]).hash,
        isNot(base),
      );
    });

    test('пересчёт объёма меняет отпечаток, а прочее сохраняет', () {
      final resized = plan().copyWith(quantity: 12, riskRubles: 4800);
      expect(resized.hash, isNot(plan().hash));
      expect(resized.stop, plan().stop);
      expect(resized.targets.length, 3);
    });
  });

  group('Бюджет риска (ТЗ §20)', () {
    RiskBudget budget({
      double score = 1.0,
      double daily = 3.0,
      double weekly = 5.0,
      double monthly = 10.0,
      double open = 2.0,
      double cluster = 1.25,
    }) =>
        RiskBudget(
          scoreRiskPercent: score,
          remainingDailyPercent: daily,
          remainingWeeklyPercent: weekly,
          remainingMonthlyPercent: monthly,
          remainingOpenRiskPercent: open,
          remainingClusterPercent: cluster,
        );

    test('бюджет — минимум из остатков', () {
      expect(budget().percent, 1.0);
      expect(budget(open: 0.4).percent, 0.4);
    });

    test('ограничитель называется по имени', () {
      // Владелец должен видеть не «нельзя», а «дневной лимит».
      expect(budget(daily: 0.2).binding.name, 'дневной лимит');
      expect(budget(cluster: 0.1).binding.name, 'риск кластера');
      expect(budget().binding.name, 'оценка идеи');
    });

    test('жёсткий лимит сильнее высокой оценки', () {
      // ТЗ §20: hard limits имеют приоритет над score.
      expect(budget(score: 1.0, daily: 0).percent, 0);
      expect(budget(score: 1.0, daily: 0).blocked, isTrue);
    });

    test('исчерпанный лимит не даёт отрицательного бюджета', () {
      expect(budget(daily: -1.5).percent, 0);
    });

    test('значения лимитов ровно из ТЗ', () {
      expect(RiskBudget.dailyLossPercent, 3.0);
      expect(RiskBudget.weeklyLossPercent, 5.0);
      expect(RiskBudget.monthlyLossPercent, 10.0);
      expect(RiskBudget.openRiskPercent, 2.0);
      expect(RiskBudget.clusterRiskPercent, 1.25);
    });
  });

  group('Расчёт объёма (ТЗ §20.1)', () {
    test('объём округляется вниз до кратности лота', () {
      // Бюджет 4000 ₽, риск на единицу 400 ₽ → ровно 10 единиц.
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: 4000, priceRisk: 4, valuePerPoint: 100, lotSize: 1),
        10,
      );
      // 4300 ₽ дало бы 10,75 — берём 10, а не 11: превысить бюджет нельзя.
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: 4300, priceRisk: 4, valuePerPoint: 100, lotSize: 1),
        10,
      );
      // Лот 5 — округляем до 10, а не до 10,75.
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: 5900, priceRisk: 4, valuePerPoint: 100, lotSize: 5),
        10,
      );
    });

    test('бюджет меньше лота даёт ноль', () {
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: 300, priceRisk: 4, valuePerPoint: 100, lotSize: 1),
        0,
      );
    });

    test('нулевые и отрицательные входные не ломают расчёт', () {
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: 4000, priceRisk: 0, valuePerPoint: 100, lotSize: 1),
        0,
      );
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: -1, priceRisk: 4, valuePerPoint: 100, lotSize: 1),
        0,
      );
      expect(
        PlanSizing.quantity(
            riskBudgetRubles: 4000, priceRisk: 4, valuePerPoint: 100, lotSize: 0),
        0,
      );
    });

    test('фактический риск объёма не превышает бюджета', () {
      const budget = 4300.0;
      final q = PlanSizing.quantity(
          riskBudgetRubles: budget, priceRisk: 4, valuePerPoint: 100, lotSize: 1);
      final actual =
          PlanSizing.riskOf(quantity: q, priceRisk: 4, valuePerPoint: 100);
      expect(actual, lessThanOrEqualTo(budget));
      expect(actual, 4000);
    });
  });
}
