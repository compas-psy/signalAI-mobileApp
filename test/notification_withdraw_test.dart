/// Снятие пуша по отработавшей идее.
///
/// Система хранит показанное, пока его не смахнут руками. Идея уходит из
/// выдачи — отработала, протухла, перестала проходить допуск, — а её
/// уведомление остаётся навсегда. Владелец открывает такой пуш и не
/// находит там ничего: врёт не текст, а сам факт присутствия — пуш
/// утверждает, что есть повод действовать.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/native_bridge.dart';

void main() {
  group('Идентификатор уведомления', () {
    test('устойчив: повтор заменяет, а не ложится рядом', () {
      // Порядковый счётчик означал две беды сразу: дубли в шторке и
      // невозможность снять — связи между идеей и её пушем не было.
      expect(
        NativeBridge.noticeId('signal:SBER@300.5'),
        NativeBridge.noticeId('signal:SBER@300.5'),
      );
    });

    test('разные идеи получают разные номера', () {
      expect(
        NativeBridge.noticeId('signal:SBER@300.5'),
        isNot(NativeBridge.noticeId('signal:SBER@301.0')),
      );
      expect(
        NativeBridge.noticeId('signal:GAZP@120'),
        isNot(NativeBridge.noticeId('signal:SBER@120')),
      );
    });

    test('номер не спорит с чужими уведомлениями', () {
      // Служебные каналы приложения занимают низкие номера; попасть в них
      // значит погасить чужое уведомление вместо своего.
      final id = NativeBridge.noticeId('signal:SBER@300.5');
      expect(id, greaterThanOrEqualTo(1000));
      expect(id, lessThan(10000));
    });

    test('пустой ключ не роняет вычисление', () {
      expect(NativeBridge.noticeId(''), isPositive);
    });
  });
}
