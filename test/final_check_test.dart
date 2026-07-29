import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/final_check.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';

import 'support/score_fixture.dart';
import 'package:signalai/domain/idea/trade_plan.dart';

final created = DateTime.utc(2026, 7, 29, 10);
final expires = DateTime.utc(2026, 7, 29, 18);
final now = DateTime.utc(2026, 7, 29, 12);


TradePlan samplePlan({
  double riskPercent = 0.75,
  double marginEstimate = 25000,
  double stop = 96,
  List<PlanTarget>? targets,
}) =>
    TradePlan(
      instrumentId: 'SiZ6',
      direction: Direction.long,
      orderType: PlanOrderType.limit,
      entryLow: 99,
      entryHigh: 101,
      maxSlippagePercent: 0.15,
      stop: stop,
      targets: targets ??
          const [
            PlanTarget(name: 'TP1', price: 108, fraction: 0.4, afterFill: ''),
            PlanTarget(name: 'TP2', price: 112, fraction: 0.4, afterFill: ''),
            PlanTarget(name: 'TP3', price: 120, fraction: 0.2, afterFill: ''),
          ],
      quantity: 10,
      lotSize: 1,
      valuePerPoint: 100,
      riskRubles: 4000,
      riskPercent: riskPercent,
      marginEstimate: marginEstimate,
      expiry: expires,
      strategyVersion: 'trend-pullback-1.0.0',
    );

Idea idea({
  IdeaState state = IdeaState.triggered,
  int score = 85,
  TradePlan? plan,
  EventRisk? event,
  DateTime? validUntil,
}) =>
    Idea(
      id: 'idea_1',
      instrumentId: 'SiZ6',
      instrumentName: 'Si-12.26',
      market: Market.forts,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'trend-pullback-1.0.0',
      state: state,
      score: scoreOf(score),
      createdAt: created,
      validUntil: validUntil ?? expires,
      thesis: 'Откат в зону спроса.',
      plan: plan ?? samplePlan(),
      eventRisk: event,
    );

RiskBudget budget({double percent = 1.0}) => RiskBudget(
      scoreRiskPercent: percent,
      remainingDailyPercent: 3,
      remainingWeeklyPercent: 5,
      remainingMonthlyPercent: 10,
      remainingOpenRiskPercent: 2,
      remainingClusterPercent: 1.25,
    );

ExecutionContext ctx({
  double? lastPrice = 100,
  double? freeMargin = 100000,
  double? cluster = 1.0,
  String? shownHash,
  RiskBudget? riskBudget,
  bool tradingOpen = true,
  bool orderBookHealthy = true,
  bool protectiveOrders = true,
  bool paperMode = false,
  DateTime? at,
  TradePlan? plan,
}) =>
    ExecutionContext(
      now: at ?? now,
      lastPrice: lastPrice,
      budget: riskBudget ?? budget(),
      freeMargin: freeMargin,
      clusterRiskAfterPercent: cluster,
      shownPlanHash: shownHash ?? (plan ?? samplePlan()).hash,
      tradingOpen: tradingOpen,
      orderBookHealthy: orderBookHealthy,
      protectiveOrdersSupported: protectiveOrders,
      paperMode: paperMode,
    );

CheckResult? find(List<CheckResult> results, CheckKind kind) {
  final matching = results.where((r) => r.kind == kind && !r.passed);
  if (matching.isNotEmpty) return matching.first;
  final any = results.where((r) => r.kind == kind);
  return any.isEmpty ? null : any.first;
}

void main() {
  group('Здоровая идея', () {
    test('проходит все проверки', () {
      final results = FinalCheck.run(idea(), ctx());
      expect(FinalCheck.blockers(results), isEmpty,
          reason: FinalCheck.blockers(results).map((b) => b.detail).join('; '));
      expect(FinalCheck.passes(results), isTrue);
    });

    test('проверяются все пункты таблицы ТЗ §11.1', () {
      // Пропущенная проверка — это дыра в гейте. Список закрыт.
      final kinds = FinalCheck.run(idea(), ctx()).map((r) => r.kind).toSet();
      expect(kinds, containsAll(CheckKind.values));
    });
  });

  group('Версия идеи (ТЗ §11.1)', () {
    test('пересчитанный план не подтверждается по старому отпечатку', () {
      // Классическая подмена: показали одно, подтвердили другое.
      final results = FinalCheck.run(idea(), ctx(shownHash: 'deadbeef'));
      final check = find(results, CheckKind.ideaVersion)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('план пересчитан'));
    });
  });

  group('Срок и состояние', () {
    test('истёкший сигнал не подтверждается', () {
      final results = FinalCheck.run(idea(), ctx(at: DateTime.utc(2026, 7, 29, 19)));
      expect(find(results, CheckKind.ttl)!.passed, isFalse);
    });

    test('подтверждать можно только Triggered', () {
      for (final state in IdeaState.values) {
        final results = FinalCheck.run(idea(state: state), ctx());
        expect(find(results, CheckKind.state)!.passed, state.canConfirm,
            reason: state.label);
      }
    });

    test('идея без плана не доходит до денежных проверок', () {
      final noPlan = Idea(
        id: 'i',
        instrumentId: 'SiZ6',
        instrumentName: 'Si',
        market: Market.forts,
        direction: Direction.long,
        strategy: SetupStrategy.trendPullback,
        strategyVersion: 'v1',
        state: IdeaState.triggered,
        score: scoreOf(85),
        createdAt: created,
        validUntil: expires,
        thesis: '',
        plan: null,
      );
      final results = FinalCheck.run(noPlan, ctx());
      expect(FinalCheck.passes(results), isFalse);
      expect(find(results, CheckKind.sizing)!.detail, contains('плана нет'));
    });
  });

  group('Цена и проскальзывание', () {
    test('цена вне зоны с учётом допуска блокирует вход', () {
      // Допуск 0,15% от 100 — это 0,15. Зона 99–101, значит 101,2 уже вне.
      expect(find(FinalCheck.run(idea(), ctx(lastPrice: 101.1)), CheckKind.price)!.passed,
          isTrue);
      final far = find(FinalCheck.run(idea(), ctx(lastPrice: 101.5)), CheckKind.price)!;
      expect(far.passed, isFalse);
      expect(far.detail, contains('вне зоны'));
    });

    test('отсутствие котировки — не «пройдено», а «неизвестно»', () {
      // ТЗ §27: отсутствие данных не маскируется под норму.
      final check = find(FinalCheck.run(idea(), ctx(lastPrice: null)), CheckKind.price)!;
      expect(check.skipped, isTrue);
      expect(check.passed, isFalse);
      expect(FinalCheck.passes(FinalCheck.run(idea(), ctx(lastPrice: null))), isFalse);
    });
  });

  group('Риск и объём', () {
    test('риск выше доступного бюджета блокирует', () {
      final results = FinalCheck.run(
        idea(plan: samplePlan(riskPercent: 1.0)),
        ctx(riskBudget: budget(percent: 0.5), plan: samplePlan(riskPercent: 1.0)),
      );
      final check = find(results, CheckKind.riskLimits)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('выше доступного'));
    });

    test('исчерпанный лимит блокирует независимо от оценки', () {
      final zero = RiskBudget(
        scoreRiskPercent: 1,
        remainingDailyPercent: 0,
        remainingWeeklyPercent: 5,
        remainingMonthlyPercent: 10,
        remainingOpenRiskPercent: 2,
        remainingClusterPercent: 1.25,
      );
      final check = find(
          FinalCheck.run(idea(score: 100), ctx(riskBudget: zero)), CheckKind.riskLimits)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('дневной лимит'));
    });

    test('оценка ниже 75 не даёт войти', () {
      final results = FinalCheck.run(idea(score: 70), ctx());
      final check = find(results, CheckKind.riskLimits)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('только наблюдение'));
    });

    test('план без стопа блокируется по объёму и стопу', () {
      final broken = samplePlan(stop: 100);
      final results = FinalCheck.run(idea(plan: broken), ctx(plan: broken));
      expect(find(results, CheckKind.sizing)!.passed, isFalse);
    });

    test('R/R ниже порога блокируется отдельным пунктом', () {
      final thin = samplePlan(targets: const [
        PlanTarget(name: 'TP1', price: 104, fraction: 0.5, afterFill: ''),
        PlanTarget(name: 'TP2', price: 106, fraction: 0.5, afterFill: ''),
      ]);
      final results = FinalCheck.run(idea(plan: thin), ctx(plan: thin));
      expect(find(results, CheckKind.riskReward)!.passed, isFalse);
      // Плохой R/R не должен заодно ронять проверку объёма: владелец увидит
      // две беды там, где она одна.
      expect(find(results, CheckKind.sizing)!.passed, isTrue);
    });
  });

  group('Маржа и корреляция', () {
    test('нехватка маржи блокирует', () {
      final check = find(FinalCheck.run(idea(), ctx(freeMargin: 1000)), CheckKind.margin)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('нужно'));
    });

    test('молчание брокера о марже блокирует как неизвестное', () {
      final check =
          find(FinalCheck.run(idea(), ctx(freeMargin: null)), CheckKind.margin)!;
      expect(check.skipped, isTrue);
      expect(check.passed, isFalse);
    });

    test('кластер выше 1% блокирует', () {
      final check =
          find(FinalCheck.run(idea(), ctx(cluster: 1.6)), CheckKind.correlation)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('выше'));
      // Ровно на границе — проходит.
      expect(find(FinalCheck.run(idea(), ctx(cluster: 1.0)), CheckKind.correlation)!.passed,
          isTrue);
    });
  });

  group('Событие, рынок и защита', () {
    test('политика события запрещает вход', () {
      final blocked = EventRisk(
        title: 'Заседание ЦБ',
        at: DateTime.utc(2026, 7, 29, 13, 30),
        impact: EventImpact.high,
        policy: 'вход запрещён за час до публикации',
        blocksEntry: true,
      );
      final check =
          find(FinalCheck.run(idea(event: blocked), ctx()), CheckKind.eventWindow)!;
      expect(check.passed, isFalse);
      expect(check.detail, contains('Заседание ЦБ'));
    });

    test('событие без запрета не блокирует, но названо', () {
      final soft = EventRisk(
        title: 'Отчёт по запасам',
        at: DateTime.utc(2026, 7, 29, 17, 30),
        impact: EventImpact.mid,
        policy: 'риск сокращается вдвое',
      );
      final check =
          find(FinalCheck.run(idea(event: soft), ctx()), CheckKind.eventWindow)!;
      expect(check.passed, isTrue);
      expect(check.detail, contains('Отчёт по запасам'));
    });

    test('закрытые торги и больной стакан блокируют', () {
      expect(
          find(FinalCheck.run(idea(), ctx(tradingOpen: false)), CheckKind.marketState)!
              .passed,
          isFalse);
      expect(
          find(FinalCheck.run(idea(), ctx(orderBookHealthy: false)),
                  CheckKind.marketState)!
              .passed,
          isFalse);
    });

    test('бумажный режим не выдаёт отсутствие брокера за отказ', () {
      // Иначе приложение блокировало бы вход навсегда: маржи и защитных
      // заявок в бумажном режиме нет по построению, а не по сбою.
      final results = FinalCheck.run(
        idea(),
        ctx(freeMargin: null, protectiveOrders: false, paperMode: true),
      );
      expect(find(results, CheckKind.margin)!.passed, isTrue);
      expect(find(results, CheckKind.protectiveOrders)!.passed, isTrue);
      expect(FinalCheck.passes(results), isTrue);
    });

    test('в живом режиме молчание брокера по-прежнему блокирует', () {
      final results = FinalCheck.run(idea(), ctx(freeMargin: null));
      expect(find(results, CheckKind.margin)!.skipped, isTrue);
      expect(FinalCheck.passes(results), isFalse);
    });

    test('без гарантии защитной заявки вход запрещён', () {
      // ТЗ §21: если стоп не создан после входа — компенсирующее закрытие.
      // Проще не входить, чем разбираться с голой позицией.
      final check = find(FinalCheck.run(idea(), ctx(protectiveOrders: false)),
          CheckKind.protectiveOrders)!;
      expect(check.passed, isFalse);
    });
  });

  test('отказы возвращаются все сразу, а не первый', () {
    // Иначе владелец чинит препятствия по одному вслепую.
    final results = FinalCheck.run(
      idea(state: IdeaState.ready, score: 70),
      ctx(lastPrice: 120, freeMargin: 100, cluster: 3, protectiveOrders: false),
    );
    final blockers = FinalCheck.blockers(results).map((b) => b.kind).toSet();
    expect(
        blockers,
        containsAll({
          CheckKind.state,
          CheckKind.price,
          CheckKind.riskLimits,
          CheckKind.margin,
          CheckKind.correlation,
          CheckKind.protectiveOrders,
        }));
  });
}
