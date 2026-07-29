import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/execution.dart';

final t0 = DateTime.utc(2026, 7, 29, 12);

Execution fresh({int planned = 10, ExecutionState? state}) => Execution(
      ideaId: 'idea_1',
      planHash: 'abcd1234',
      plannedQuantity: planned,
      state: state ?? ExecutionState.pendingConfirmation,
    );

/// Доводит исполнение до отправленного входа — общий пролог половины тестов.
Execution submitted({int planned = 10}) => fresh(planned: planned)
    .moveTo(ExecutionState.precheck)
    .moveTo(ExecutionState.submitEntry);

Fill fill(int qty, {double price = 100, int minute = 0, double fee = 0}) =>
    Fill(quantity: qty, price: price, at: t0.add(Duration(minutes: minute)), fee: fee);

void main() {
  group('Машина исполнения (ТЗ §11.3)', () {
    test('путь целиком проходится по порядку', () {
      var e = fresh()
          .moveTo(ExecutionState.precheck)
          .moveTo(ExecutionState.submitEntry);
      e = e.addFill(fill(10));
      expect(e.state, ExecutionState.entryFilled);
      e = e
          .moveTo(ExecutionState.placeProtection)
          .moveTo(ExecutionState.active)
          .moveTo(ExecutionState.tp1)
          .moveTo(ExecutionState.stopAtBreakeven)
          .moveTo(ExecutionState.tp2)
          .moveTo(ExecutionState.trailing)
          .moveTo(ExecutionState.closed);
      expect(e.state, ExecutionState.closed);
      expect(e.state.isTerminal, isTrue);
    });

    test('через постановку защиты перепрыгнуть нельзя', () {
      // Позиция без стопа — единственный способ потерять больше плана.
      final filled = submitted().addFill(fill(10));
      expect(() => filled.moveTo(ExecutionState.active),
          throwsA(isA<StateError>()));
      expect(() => filled.moveTo(ExecutionState.tp1),
          throwsA(isA<StateError>()));
    });

    test('запрещённый переход — ошибка, а не тихий пропуск', () {
      expect(() => fresh().moveTo(ExecutionState.active),
          throwsA(isA<StateError>()));
    });

    test('из безопасного состояния только закрытие', () {
      final safe = submitted().toSafeState('брокер молчит');
      expect(safe.state, ExecutionState.safeState);
      expect(safe.state.allowedNext, {ExecutionState.closed});
      expect(() => safe.moveTo(ExecutionState.active),
          throwsA(isA<StateError>()));
    });

    test('ошибка доступна из любого состояния', () {
      for (final state in ExecutionState.values) {
        final e = Execution(
          ideaId: 'i',
          planHash: 'h',
          plannedQuantity: 10,
          state: state,
        ).toSafeState('сбой');
        expect(e.state, ExecutionState.safeState, reason: state.label);
      }
    });

    test('состояние переживает сериализацию', () {
      final e = submitted().addFill(fill(4, fee: 12));
      final back = Execution.fromJson(e.toJson());
      expect(back.state, e.state);
      expect(back.filledQuantity, 4);
      expect(back.fees, 12);
      expect(back.planHash, e.planHash);
      expect(ExecutionState.parse('нет такого'),
          ExecutionState.pendingConfirmation);
    });
  });

  group('Частичное исполнение (ТЗ §21)', () {
    test('часть объёма переводит в частичный вход', () {
      final e = submitted().addFill(fill(4));
      expect(e.state, ExecutionState.entryPartial);
      expect(e.filledQuantity, 4);
      expect(e.remainingQuantity, 6);
      expect(e.isFullyFilled, isFalse);
    });

    test('защита ставится на набранный объём, а не на плановый', () {
      // Стоп на десять контрактов при четырёх набранных — это шорт на шесть.
      final e = submitted().addFill(fill(4));
      expect(e.protectiveQuantity, 4);
      expect(e.protectiveQuantity, lessThan(e.plannedQuantity));
    });

    test('добор до полного объёма закрывает вход', () {
      final e = submitted().addFill(fill(4)).addFill(fill(6, minute: 3));
      expect(e.state, ExecutionState.entryFilled);
      expect(e.protectiveQuantity, 10);
      expect(e.entryFilledAt, t0.add(const Duration(minutes: 3)));
    });

    test('средняя цена входа взвешена объёмом', () {
      final e = submitted()
          .addFill(fill(2, price: 100))
          .addFill(fill(8, price: 110, minute: 1));
      expect(e.averageEntry, closeTo(108, 1e-9));
    });

    test('перебор объёма уводит в безопасное состояние', () {
      // Исполнено больше, чем просили: это ошибка брокера или наша, и
      // торговать дальше по такому состоянию нельзя.
      final e = submitted().addFill(fill(12));
      expect(e.state, ExecutionState.safeState);
      expect(e.note, contains('исполнено 12'));
    });

    test('исполнение в неподходящем состоянии не проглатывается', () {
      final active = submitted()
          .addFill(fill(10))
          .moveTo(ExecutionState.placeProtection)
          .moveTo(ExecutionState.active)
          .moveTo(ExecutionState.tp1);
      final stray = active.addFill(fill(1, minute: 5));
      expect(stray.state, ExecutionState.safeState);
    });
  });

  group('Защита и SLA (ТЗ §21)', () {
    test('голая позиция после срока требует аварийного закрытия', () {
      final naked = submitted().addFill(fill(10));
      expect(naked.state.isNaked, isTrue);
      expect(
        ExecutionRules.needsCompensatingClose(
            naked, t0.add(const Duration(seconds: 31))),
        isTrue,
      );
      expect(
        ExecutionRules.needsCompensatingClose(
            naked, t0.add(const Duration(seconds: 10))),
        isFalse,
      );
    });

    test('поставленная защита снимает требование', () {
      final protectedPosition = submitted()
          .addFill(fill(10))
          .copyWith(protection: ProtectionStatus.placed);
      expect(
        ExecutionRules.needsCompensatingClose(
            protectedPosition, t0.add(const Duration(minutes: 5))),
        isFalse,
      );
    });

    test('без позиции аварийное закрытие не нужно', () {
      expect(
        ExecutionRules.needsCompensatingClose(
            submitted(), t0.add(const Duration(hours: 1))),
        isFalse,
      );
    });

    test('состояния честно делятся на голые и защищённые', () {
      expect(ExecutionState.entryFilled.isNaked, isTrue);
      expect(ExecutionState.placeProtection.isNaked, isTrue);
      expect(ExecutionState.active.isNaked, isFalse);
      expect(ExecutionState.active.isProtected, isTrue);
      expect(ExecutionState.closed.hasPosition, isFalse);
    });
  });

  group('Правила сопровождения (ТЗ §21.1, §13.1)', () {
    test('после TP1 риск не увеличивается', () {
      expect(ExecutionRules.canIncreaseRisk(ExecutionState.entryPartial), isTrue);
      expect(ExecutionRules.canIncreaseRisk(ExecutionState.tp1), isFalse);
      expect(ExecutionRules.canIncreaseRisk(ExecutionState.trailing), isFalse);
    });

    test('безубыток учитывает комиссии, а не равен входу', () {
      // Выход ровно по цене входа оставляет минус на величину издержек.
      expect(
        ExecutionRules.breakevenStop(
            averageEntry: 100, feesPerUnit: 0.4, long: true),
        closeTo(100.4, 1e-9),
      );
      expect(
        ExecutionRules.breakevenStop(
            averageEntry: 100, feesPerUnit: 0.4, long: false),
        closeTo(99.6, 1e-9),
      );
    });

    test('у каждого состояния есть человеческое действие', () {
      for (final state in ExecutionState.values) {
        expect(ExecutionRules.actionOn(state), isNotEmpty, reason: state.label);
      }
    });
  });

  group('Идемпотентность (ТЗ §21)', () {
    test('ключ операции меняется вместе с версией', () {
      // Повторная отправка того же шага не должна создать вторую заявку,
      // но следующий шаг обязан получить свой ключ.
      final e = submitted();
      expect(e.idempotencyKey('entry'), e.idempotencyKey('entry'));
      expect(e.idempotencyKey('entry'), isNot(e.idempotencyKey('protection')));

      final next = e.addFill(fill(10));
      expect(next.idempotencyKey('entry'), isNot(e.idempotencyKey('entry')));
      expect(next.version, greaterThan(e.version));
    });
  });
}
