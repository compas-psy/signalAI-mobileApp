import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';

import 'support/score_fixture.dart';

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

  group('Оценка идеи (engine-ТЗ §15.1)', () {
    test('порог показа и порог входа — ровно из ТЗ', () {
      expect(scoreOf(64).value, 64);
      expect(scoreOf(70).riskPercent, isNull,
          reason: '65–74 — только наблюдение, риск не выделяется');
      expect(scoreOf(74).tradable, isFalse);
      expect(scoreOf(75).tradable, isTrue);
    });

    test('риск на сделку — две ступени engine-ТЗ §17', () {
      // Ступени 1% из UX-ТЗ здесь нет: на числах побеждает engine-ТЗ
      // (docs/TZ_PRIORITY.md). Разница в деньгах прямая — процент капитала.
      expect(scoreOf(77).riskPercent, 0.50);
      expect(scoreOf(80).riskPercent, 0.75, reason: 'A-grade начинается с 80');
      expect(scoreOf(100).riskPercent, 0.75, reason: 'выше 0,75% ступеней нет');
    });

    test('оценка не выдаётся за вероятность', () {
      // §15.1 против §15.3: балл и вероятность — утверждения разной силы.
      expect(scoreOf(90).label, isNot(contains('вероятн')));
      expect(scoreOf(90).label, isNot(contains('%')));
    });

    test('неизмеренное не читается как слабое', () {
      final score = scoreWithMissing(
          80, ScoreComponentKind.derivativesConfirmation);
      expect(score.hasUnmeasured, isTrue);
      expect(score.unmeasured.single.kind,
          ScoreComponentKind.derivativesConfirmation);
      expect(score.dataQuality, lessThan(1.0),
          reason: 'дыра в данных обязана быть видна в множителе');
    });

    test('неприменимое не попадает в слабые места', () {
      // Открытого интереса у акции не бывает. Считать это изъяном идеи
      // значит наказывать её за то, чего в природе нет.
      final base = scoreOf(80);
      final withNa = QualityScore(
        total: base.total,
        contributions: [
          for (final c in base.contributions)
            if (c.kind == ScoreComponentKind.derivativesConfirmation)
              ScoreContribution(
                kind: c.kind,
                value: 0,
                effectiveWeight: 0,
                points: 0,
                status: MeasurementStatus.notApplicable,
              )
            else
              c,
        ],
      );
      expect(
        withNa.weakest.map((c) => c.kind),
        isNot(contains(ScoreComponentKind.derivativesConfirmation)),
      );
    });

    test('одиннадцать компонентов, веса из ТЗ и сумма единица', () {
      expect(ScoreComponentKind.values.length, 11);
      final sum = ScoreComponentKind.values
          .fold<double>(0, (a, k) => a + k.weight);
      expect(sum, closeTo(1.0, 1e-9));
    });
  });
}
