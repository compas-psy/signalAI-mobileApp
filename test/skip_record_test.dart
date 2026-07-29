import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';

import 'support/score_fixture.dart';
import 'package:signalai/domain/idea/skip_record.dart';
import 'package:signalai/domain/idea/trade_plan.dart';

final at = DateTime.utc(2026, 7, 29, 14, 5);


Idea idea({String id = 'idea_1', int score = 82}) => Idea(
      id: id,
      instrumentId: 'SiZ6',
      instrumentName: 'Si-12.26',
      market: Market.forts,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'v1',
      state: IdeaState.triggered,
      score: scoreOf(score),
      createdAt: at.subtract(const Duration(hours: 4)),
      validUntil: at.add(const Duration(hours: 4)),
      thesis: '',
      plan: TradePlan(
        instrumentId: 'SiZ6',
        direction: Direction.long,
        orderType: PlanOrderType.limit,
        entryLow: 99,
        entryHigh: 101,
        maxSlippagePercent: 0.15,
        stop: 96,
        targets: const [
          PlanTarget(name: 'TP1', price: 108, fraction: 0.5, afterFill: ''),
          PlanTarget(name: 'TP2', price: 112, fraction: 0.5, afterFill: ''),
        ],
        quantity: 10,
        lotSize: 1,
        valuePerPoint: 100,
        riskRubles: 4000,
        riskPercent: 0.75,
        marginEstimate: 25000,
        expiry: at.add(const Duration(hours: 4)),
        strategyVersion: 'v1',
      ),
    );

SkipRecord record(SkipReason reason, {String comment = ''}) =>
    SkipRecord.of(idea(), reason: reason, comment: comment, at: at);

void main() {
  group('Запись пропуска (ТЗ §12)', () {
    test('снимок хранит условия отказа, а не ссылку на идею', () {
      // Идея живёт восемь часов, запись — навсегда. Ссылка на исчезнувший
      // объект оставила бы в журнале строку без содержания.
      final r = record(SkipReason.noTrust, comment: 'не верю в разворот');
      expect(r.instrumentId, 'SiZ6');
      expect(r.direction, 'LONG');
      expect(r.score, 82);
      expect(r.riskPercent, 0.75);
      expect(r.rrToSecondTarget, closeTo(3.0, 1e-9));
      expect(r.strategy, SetupStrategy.trendPullback.label);
    });

    test('запись переживает сериализацию', () {
      final source = record(SkipReason.lateEntry, comment: 'ушла без меня');
      final back = SkipRecord.fromJson(source.toJson());
      expect(back.ideaId, source.ideaId);
      expect(back.reason, SkipReason.lateEntry);
      expect(back.comment, 'ушла без меня');
      expect(back.at, source.at);
      expect(back.score, source.score);
      expect(back.riskPercent, source.riskPercent);
    });

    test('неизвестный код причины не теряет запись', () {
      final broken = SkipRecord.fromJson({
        'idea_id': 'x',
        'instrument': 'BR',
        'reason': 'ЧТО-ТО_НОВОЕ',
        'at': at.toIso8601String(),
      });
      expect(broken.reason, SkipReason.other);
      expect(broken.instrumentId, 'BR');
    });
  });

  group('Разрез по причинам', () {
    test('причины ранжируются от частых к редким', () {
      final breakdown = SkipBreakdown.of([
        record(SkipReason.notSeen),
        record(SkipReason.notSeen),
        record(SkipReason.notSeen),
        record(SkipReason.noTrust),
        record(SkipReason.riskLimit),
        record(SkipReason.riskLimit),
      ]);
      expect(breakdown.total, 6);
      expect(breakdown.ranked.first.key, SkipReason.notSeen);
      expect(breakdown.ranked.first.value, 3);
      expect(breakdown.ranked[1].key, SkipReason.riskLimit);
      expect(breakdown.share(SkipReason.notSeen), closeTo(0.5, 1e-9));
    });

    test('причины без единого случая не показываются', () {
      // Строка «Психологическая причина — 0» ничего не сообщает и растягивает
      // список до семи пунктов, из которых значат что-то два.
      final breakdown = SkipBreakdown.of([record(SkipReason.technical)]);
      expect(breakdown.ranked.length, 1);
      expect(breakdown.share(SkipReason.psychological), 0);
    });

    test('пустой журнал не делит на ноль', () {
      final breakdown = SkipBreakdown.of(const []);
      expect(breakdown.total, 0);
      expect(breakdown.ranked, isEmpty);
      expect(breakdown.share(SkipReason.other), 0);
    });
  });
}
