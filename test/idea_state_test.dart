import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';

FactorContribution part(ScoreFactor factor, double fraction) =>
    FactorContribution(factor: factor, fraction: fraction, note: '');

QualityScore scoreOf(double fraction) => QualityScore(
      contributions: [
        for (final f in ScoreFactor.values) part(f, fraction),
      ],
    );

void main() {
  group('Машина состояний идеи (ТЗ §8)', () {
    test('подтвердить можно только Triggered', () {
      // В Ready «не хватает подтверждения», Expired и Invalidated прямо
      // помечены как «нельзя исполнить». Если это правило живёт в экранах,
      // однажды кнопка на одном из них подтвердит истёкший сигнал.
      for (final state in IdeaState.values) {
        expect(state.canConfirm, state == IdeaState.triggered,
            reason: state.label);
      }
    });

    test('истёкшая и сломанная идея не исполняются', () {
      expect(IdeaState.expired.canConfirm, isFalse);
      expect(IdeaState.invalidated.canConfirm, isFalse);
      expect(IdeaState.expired.canSkip, isFalse);
    });

    test('переходы разрешены только вперёд по циклу', () {
      expect(IdeaState.watch.canMoveTo(IdeaState.ready), isTrue);
      expect(IdeaState.ready.canMoveTo(IdeaState.triggered), isTrue);
      expect(IdeaState.triggered.canMoveTo(IdeaState.active), isTrue);
      expect(IdeaState.active.canMoveTo(IdeaState.closed), isTrue);

      // Прыжок через состояние недопустим: Watch не может стать Active.
      expect(IdeaState.watch.canMoveTo(IdeaState.active), isFalse);
      expect(IdeaState.ready.canMoveTo(IdeaState.active), isFalse);
    });

    test('терминальные состояния никуда не ведут', () {
      // Журнал неизменяем: закрытая или пропущенная идея не оживает.
      for (final state in IdeaState.values.where((s) => s.isTerminal)) {
        expect(state.allowedNext, isEmpty, reason: state.label);
      }
    });

    test('активная идея закрывается, но не пропускается', () {
      expect(IdeaState.active.canMoveTo(IdeaState.skipped), isFalse,
          reason: 'позиция уже открыта — пропустить её нельзя');
      expect(IdeaState.active.canSkip, isFalse);
    });

    test('внимания требуют Ready и Triggered', () {
      final attention =
          IdeaState.values.where((s) => s.needsAttention).toSet();
      expect(attention, {IdeaState.ready, IdeaState.triggered});
    });

    test('состояние переживает сериализацию, мусор даёт Watch', () {
      for (final state in IdeaState.values) {
        expect(IdeaState.parse(state.name), state);
      }
      expect(IdeaState.parse('нет такого'), IdeaState.watch);
      expect(IdeaState.parse(null), IdeaState.watch);
    });
  });

  group('Причина пропуска (приложение A)', () {
    test('справочник закрыт и коды не пересекаются', () {
      final codes = {for (final r in SkipReason.values) r.code};
      expect(codes.length, SkipReason.values.length);
    });

    test('«Другое» требует комментария', () {
      // Без пояснения эта причина ничего не сообщает и портит разрезы.
      expect(SkipReason.other.needsComment, isTrue);
      expect(SkipReason.riskLimit.needsComment, isFalse);
    });

    test('неизвестный код не теряется, а становится «Другое»', () {
      expect(SkipReason.parse('WHATEVER'), SkipReason.other);
    });
  });

  group('Quality score (ТЗ §14.2)', () {
    test('веса факторов дают ровно сто', () {
      // Сумма не сто означает, что шкала 0–100 врёт: идеальная идея не
      // набирает максимума либо перебирает его.
      expect(ScoreFactor.totalWeight, 100);
    });

    test('полное выполнение даёт 100, пустое — 0', () {
      expect(scoreOf(1).value, 100);
      expect(scoreOf(0).value, 0);
    });

    test('маппинг оценки в риск ровно из ТЗ', () {
      expect(scoreOf(0.70).value, 70);
      expect(scoreOf(0.70).riskPercent, isNull, reason: '65–74 — только watch');

      expect(scoreOf(0.77).riskPercent, 0.50);
      expect(scoreOf(0.85).riskPercent, 0.75);
      expect(scoreOf(1.00).riskPercent, 1.00);
    });

    test('граница 75 включает риск, 74 — ещё нет', () {
      final at75 = QualityScore(contributions: [
        part(ScoreFactor.marketRegime, 1),
        part(ScoreFactor.multiTfStructure, 1),
        part(ScoreFactor.zoneQuality, 1),
        part(ScoreFactor.entryTrigger, 1),
        part(ScoreFactor.volumeOi, 1),
      ]);
      expect(at75.value, 75);
      expect(at75.riskPercent, 0.50);
      expect(at75.tradable, isTrue);

      final at74 = QualityScore(contributions: [
        ...at75.contributions.take(4),
        part(ScoreFactor.volumeOi, 14 / 15),
      ]);
      expect(at74.value, 74);
      expect(at74.tradable, isFalse);
    });

    test('оценка не выдаётся за вероятность', () {
      // ТЗ §14.3: превращать score в вероятность до калибровки нельзя.
      expect(scoreOf(0.9).label, contains('Quality score'));
      expect(scoreOf(0.9).label, isNot(contains('вероятн')));
      expect(scoreOf(0.9).label, isNot(contains('%')));
    });

    test('слабые факторы называются первыми', () {
      final score = QualityScore(contributions: [
        part(ScoreFactor.marketRegime, 1.0),
        part(ScoreFactor.multiTfStructure, 0.2),
        part(ScoreFactor.zoneQuality, 0.9),
        part(ScoreFactor.eventRisk, 0.1),
      ]);
      final weakest = score.weakest.map((c) => c.factor).toList();
      expect(weakest.first, ScoreFactor.eventRisk);
      expect(weakest[1], ScoreFactor.multiTfStructure);
    });

    test('вклад фактора ограничен его весом', () {
      // Доля выше единицы не должна прорывать шкалу.
      final over = QualityScore(contributions: [part(ScoreFactor.liquidity, 5)]);
      expect(over.value, ScoreFactor.liquidity.weight);
    });
  });
}
