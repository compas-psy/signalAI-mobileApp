/// Пустой экран пакетов обязан называть причину.
///
/// Владелец увидел зелёный конвейер, «составов 2» — и ничего под ними. Ни
/// причины, ни действия: исправная машина, которая молчит. Причина была в
/// том, что оба состава посчитаны на другой горизонт, а `reason` от движка
/// приходит пустым, когда под запрос **что-то** подошло: фильтр по горизонту
/// и варианту живёт уже на экране, и объяснить эту пустоту движок не может.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/portfolio/package_absence.dart';

void main() {
  group('Почему пусто', () {
    test('составы на другой горизонт — это причина, а не тишина', () {
      final note = packageAbsenceNote(
        readyVariants: const [],
        otherHorizons: const [5],
        horizonLabel: '1 год',
        reason: '',
      );
      expect(note, contains('1 год'));
      expect(note, contains('5 лет'));
      // Владельцу нужно действие, а не диагноз.
      expect(note, contains('Переключите'));
    });

    test('вариант не прошёл проверку — лечится соседней вкладкой', () {
      final note = packageAbsenceNote(
        readyVariants: const ['Простой', 'Максимальный'],
        otherHorizons: const [5],
        horizonLabel: '1 год',
        reason: 'что-то от движка',
      );
      expect(note, contains('простой'));
      expect(note, contains('максимальный'));
      // Про горизонт здесь ни слова: на этом горизонте составы есть.
      expect(note, isNot(contains('Переключите горизонт')));
    });

    test('не посчитано ничего — говорит движок', () {
      final note = packageAbsenceNote(
        readyVariants: const [],
        otherHorizons: const [],
        horizonLabel: '1 год',
        reason: 'работа стоит на шаге «Вселенная инструментов»',
      );
      expect(note, 'работа стоит на шаге «Вселенная инструментов»');
    });

    test('движок промолчал — экран всё равно объясняет, куда смотреть', () {
      // Пустая строка вместо причины — ровно то, что владелец и увидел.
      final note = packageAbsenceNote(
        readyVariants: const [],
        otherHorizons: const [],
        horizonLabel: '1 год',
        reason: '',
      );
      expect(note, isNotEmpty);
      expect(note, contains('Шаги сборки'));
    });

    test('несколько посчитанных горизонтов перечисляются по порядку', () {
      final note = packageAbsenceNote(
        readyVariants: const [],
        otherHorizons: const [10, 3],
        horizonLabel: '1 год',
        reason: '',
      );
      expect(note, contains('3 и 10 лет'));
    });
  });

  group('Падеж по числу лет', () {
    test('единица, малые и большие числа', () {
      expect(yearsWord(1), 'год');
      expect(yearsWord(3), 'года');
      expect(yearsWord(5), 'лет');
      // Одиннадцать — не «один»: правило по последней цифре здесь ломается.
      expect(yearsWord(11), 'лет');
      expect(yearsWord(21), 'год');
    });
  });
}
