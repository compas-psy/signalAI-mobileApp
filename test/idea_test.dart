import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/evidence.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';
import 'package:signalai/domain/idea/trade_plan.dart';

final created = DateTime.utc(2026, 7, 29, 10);
final expires = DateTime.utc(2026, 7, 29, 18);

QualityScore scoreOf(int target) => QualityScore(contributions: [
      FactorContribution(
        factor: ScoreFactor.marketRegime,
        fraction: target / ScoreFactor.marketRegime.weight,
        note: '',
      ),
      const FactorContribution(
          factor: ScoreFactor.multiTfStructure, fraction: 1, note: ''),
      const FactorContribution(
          factor: ScoreFactor.zoneQuality, fraction: 1, note: ''),
      const FactorContribution(
          factor: ScoreFactor.entryTrigger, fraction: 1, note: ''),
      const FactorContribution(
          factor: ScoreFactor.volumeOi, fraction: 1, note: ''),
      const FactorContribution(
          factor: ScoreFactor.riskReward, fraction: 1, note: ''),
      const FactorContribution(
          factor: ScoreFactor.liquidity, fraction: 1, note: ''),
      const FactorContribution(
          factor: ScoreFactor.eventRisk, fraction: 1, note: ''),
    ]);

/// Оценка ровно [value] баллов через один частично выполненный фактор.
QualityScore exactly(int value) {
  final full = <FactorContribution>[];
  var left = value;
  for (final f in ScoreFactor.values) {
    if (left >= f.weight) {
      full.add(FactorContribution(factor: f, fraction: 1, note: ''));
      left -= f.weight;
    } else if (left > 0) {
      full.add(
          FactorContribution(factor: f, fraction: left / f.weight, note: ''));
      left = 0;
    }
  }
  return QualityScore(contributions: full);
}

TradePlan samplePlan({double riskPercent = 0.75}) => TradePlan(
      instrumentId: 'SiZ6',
      direction: Direction.long,
      orderType: PlanOrderType.limit,
      entryLow: 99,
      entryHigh: 101,
      maxSlippagePercent: 0.15,
      stop: 96,
      targets: const [
        PlanTarget(name: 'TP1', price: 108, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP2', price: 112, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP3', price: 120, fraction: 0.2, afterFill: ''),
      ],
      quantity: 10,
      lotSize: 1,
      valuePerPoint: 100,
      riskRubles: 4000,
      riskPercent: riskPercent,
      marginEstimate: 25000,
      expiry: expires,
      strategyVersion: 'trend-pullback-1.0.0',
    );

Idea idea({
  String id = 'idea_1',
  IdeaState state = IdeaState.ready,
  int score = 82,
  DateTime? validUntil,
  bool riskChanged = false,
  List<Evidence> evidence = const [],
  List<ChartAnnotation> annotations = const [],
  TradePlan? plan,
}) =>
    Idea(
      id: id,
      instrumentId: 'SiZ6',
      instrumentName: 'Si-12.26',
      market: Market.forts,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'trend-pullback-1.0.0',
      state: state,
      score: exactly(score),
      createdAt: created,
      validUntil: validUntil ?? expires,
      thesis: 'Откат в зону спроса на восходящем тренде.',
      plan: plan ?? samplePlan(),
      evidence: evidence,
      annotations: annotations,
      riskChanged: riskChanged,
    );

ChartAnnotation annotation(String id, AnnotationType type, String evidenceId,
        {int priority = 50}) =>
    ChartAnnotation(
      id: id,
      type: type,
      timeframe: 'H1',
      startTime: created,
      endTime: expires,
      confidence: 0.8,
      evidenceId: evidenceId,
      detectorVersion: 'v1',
      label: type.label,
      displayPriority: priority,
    );

Evidence evidenceOf(String id, {List<String> conflicts = const []}) => Evidence(
      id: id,
      kind: 'structure',
      summary: 'структура',
      detail: '',
      confidence: 0.8,
      detectorVersion: 'v1',
      conflictsWith: conflicts,
    );

void main() {
  test('вспомогательная оценка даёт ровно заданный балл', () {
    expect(exactly(82).value, 82);
    expect(exactly(64).value, 64);
    expect(scoreOf(15).value, 100);
  });

  group('Срок жизни идеи', () {
    test('просроченная идея видна как просроченная', () {
      final i = idea();
      expect(i.isExpiredAt(DateTime.utc(2026, 7, 29, 17)), isFalse);
      expect(i.isExpiredAt(expires), isTrue);
      expect(i.remaining(DateTime.utc(2026, 7, 29, 17)), const Duration(hours: 1));
    });

    test('давление срока растёт от нуля к единице', () {
      final i = idea();
      expect(i.ttlPressure(created), 0);
      expect(i.ttlPressure(DateTime.utc(2026, 7, 29, 14)), closeTo(0.5, 1e-9));
      expect(i.ttlPressure(expires), 1);
      // За пределами срока давление не уходит выше единицы.
      expect(i.ttlPressure(DateTime.utc(2027)), 1);
    });
  });

  group('Порог показа (ТЗ §14.2)', () {
    test('идея ниже 65 не показывается вовсе', () {
      expect(idea(score: 64).showable, isFalse);
      expect(idea(score: 65).showable, isTrue);
    });

    test('слабая идея не попадает в ленту решений', () {
      final list = [idea(id: 'weak', score: 60, state: IdeaState.triggered)];
      expect(IdeaPriority.rank(list, created), isEmpty);
    });
  });

  group('Переходы состояний (ТЗ §8, §12)', () {
    test('разрешённый переход даёт новый объект, старый не меняется', () {
      // Снимок идеи неизменяем (ТЗ §17.1): переход не переписывает историю.
      final before = idea(state: IdeaState.ready);
      final after = before.moveTo(IdeaState.triggered, at: created);
      expect(before.state, IdeaState.ready);
      expect(after.state, IdeaState.triggered);
      expect(after.stateChangedAt, created);
    });

    test('запрещённый переход — ошибка, а не тихий пропуск', () {
      expect(
        () => idea(state: IdeaState.watch).moveTo(IdeaState.active, at: created),
        throwsA(isA<StateError>()),
      );
    });

    test('пропуск без причины не сохраняется', () {
      // ТЗ §12: причина из справочника обязательна, иначе разрезы журнала
      // по причинам пропуска посчитать нечем.
      expect(
        () => idea(state: IdeaState.triggered)
            .moveTo(IdeaState.skipped, at: created),
        throwsA(isA<StateError>()),
      );
    });

    test('причина «Другое» без комментария не сохраняется', () {
      expect(
        () => idea(state: IdeaState.triggered).moveTo(IdeaState.skipped,
            at: created, reason: SkipReason.other),
        throwsA(isA<StateError>()),
      );
      final ok = idea(state: IdeaState.triggered).moveTo(IdeaState.skipped,
          at: created, reason: SkipReason.other, comment: 'ждал новостей');
      expect(ok.state, IdeaState.skipped);
      expect(ok.skipComment, 'ждал новостей');
    });

    test('причина из справочника проходит без комментария', () {
      final skipped = idea(state: IdeaState.triggered).moveTo(IdeaState.skipped,
          at: created, reason: SkipReason.riskLimit);
      expect(skipped.skipReason, SkipReason.riskLimit);
    });
  });

  group('График повторяет текст (ТЗ §9.1)', () {
    final withEvidence = idea(
      evidence: [evidenceOf('e_smc'), evidenceOf('e_trend')],
      annotations: [
        annotation('a1', AnnotationType.smcOrderBlock, 'e_smc', priority: 80),
        annotation('a2', AnnotationType.trendline, 'e_trend', priority: 40),
        // Метка Вайкоффа без доказательства: детектор в оценке не участвовал.
        annotation('a3', AnnotationType.wyckoffSpring, 'e_wyckoff'),
      ],
    );

    test('метка без доказательства не рисуется', () {
      final all = withEvidence.annotationsFor(ChartLayer.values.toSet());
      expect(all.map((a) => a.id), ['a1', 'a2']);
      expect(withEvidence.availableLayers, contains(ChartLayer.smc));
      expect(withEvidence.availableLayers, isNot(contains(ChartLayer.wyckoff)));
    });

    test('выключенный слой скрывает свои метки', () {
      final only = withEvidence.annotationsFor({ChartLayer.trend});
      expect(only.map((a) => a.id), ['a2']);
    });

    test('метки идут по убыванию приоритета отрисовки', () {
      final all = withEvidence.annotationsFor(ChartLayer.values.toSet());
      expect(all.first.displayPriority, 80);
    });

    test('свечи доступны всегда', () {
      expect(idea().availableLayers, contains(ChartLayer.candles));
    });

    test('конфликт детекторов не теряется', () {
      final conflicted = idea(evidence: [
        evidenceOf('e_wyckoff', conflicts: ['e_event']),
        evidenceOf('e_event'),
      ]);
      expect(conflicted.conflicting.map((e) => e.id), ['e_wyckoff']);
    });
  });

  group('Приоритет на Today (ТЗ §6.2)', () {
    test('веса формулы ровно из ТЗ и дают единицу', () {
      expect(IdeaPriority.wUrgency, 0.35);
      expect(IdeaPriority.wQuality, 0.30);
      expect(IdeaPriority.wDecision, 0.20);
      expect(IdeaPriority.wRiskImpact, 0.15);
      expect(
        IdeaPriority.wUrgency +
            IdeaPriority.wQuality +
            IdeaPriority.wDecision +
            IdeaPriority.wRiskImpact,
        closeTo(1.0, 1e-9),
      );
    });

    test('порядок состояний соблюдается даже против оценки', () {
      // Watch с идеальной оценкой не должен вытеснять Triggered с оценкой
      // на грани: срочность решения важнее красоты сетапа.
      final list = [
        idea(id: 'watch', state: IdeaState.watch, score: 100),
        idea(id: 'trig', state: IdeaState.triggered, score: 66),
        idea(id: 'ready', state: IdeaState.ready, score: 95),
        idea(id: 'act', state: IdeaState.active, score: 90, riskChanged: true),
      ];
      final order = IdeaPriority.rank(list, created).map((i) => i.id).toList();
      expect(order, ['trig', 'ready', 'act', 'watch']);
    });

    test('внутри состояния истекающий сигнал идёт первым', () {
      final now = DateTime.utc(2026, 7, 29, 16);
      final list = [
        idea(
            id: 'fresh',
            state: IdeaState.triggered,
            validUntil: DateTime.utc(2026, 7, 30, 18)),
        idea(id: 'burning', state: IdeaState.triggered, validUntil: expires),
      ];
      expect(IdeaPriority.rank(list, now).first.id, 'burning');
    });

    test('при равной срочности выше идея с лучшей оценкой', () {
      final list = [
        idea(id: 'low', state: IdeaState.ready, score: 70),
        idea(id: 'high', state: IdeaState.ready, score: 95),
      ];
      expect(IdeaPriority.rank(list, created).first.id, 'high');
    });

    test('Today показывает не больше трёх решений', () {
      final list = [
        for (var i = 0; i < 7; i++)
          idea(id: 'i$i', state: IdeaState.triggered, score: 80 + i),
      ];
      expect(IdeaPriority.todayLimit, 3);
      expect(IdeaPriority.top(list, created).length, 3);
    });

    test('завершённые идеи в ленту решений не попадают', () {
      final list = [
        idea(id: 'closed', state: IdeaState.closed),
        idea(id: 'skipped', state: IdeaState.skipped),
        idea(id: 'expired', state: IdeaState.expired),
        idea(id: 'live', state: IdeaState.ready),
      ];
      expect(IdeaPriority.rank(list, created).map((i) => i.id), ['live']);
    });

    test('вклад риска считается от лимита открытого риска', () {
      // 1% при потолке 2% — половина свободного лимита.
      expect(IdeaPriority.riskImpact(idea(plan: samplePlan(riskPercent: 1.0))),
          closeTo(0.5, 1e-9));
      expect(IdeaPriority.riskImpact(idea(plan: samplePlan(riskPercent: 4.0))),
          1.0);
    });

    test('приоритет остаётся в границах ноль–единица', () {
      for (final state in IdeaState.values) {
        final value = IdeaPriority.of(idea(state: state, score: 100), created);
        expect(value, inInclusiveRange(0.0, 1.0), reason: state.label);
      }
    });
  });

  group('Стратегии методологии (ТЗ §14)', () {
    test('доли потока покрывают весь поток', () {
      final sum = SetupStrategy.values.fold(0.0, (s, x) => s + x.expectedShare);
      expect(sum, closeTo(1.0, 1e-9));
    });

    test('неизвестная стратегия не роняет разбор', () {
      expect(SetupStrategy.parse('нет такой'), SetupStrategy.trendPullback);
      expect(SetupStrategy.parse('wyckoffReversal'), SetupStrategy.wyckoffReversal);
    });
  });
}
