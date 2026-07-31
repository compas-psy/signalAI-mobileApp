import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/monitor/monitor_pace.dart';

/// Расход батареи в режиме постоянного наблюдения.
///
/// «Постоянное наблюдение» значило буквально: полный прогон раз в час,
/// круглосуточно, независимо от того, есть ли за чем следить и сколько
/// осталось заряда. Телефон с разряженной батареей не следит вообще ни за
/// чем — то есть режим отменял сам себя.
void main() {
  group('Шаг наблюдения', () {
    test('есть что сторожить и заряд в порядке — каждый час', () {
      final pace = paceFor(
        watching: 2,
        power: const PowerState(percent: 80),
      );
      expect(pace.sleep, const Duration(hours: 1));
      expect(pace.skipRun, isFalse);
      expect(pace.reason, contains('каждый час'));
    });

    test('сторожить нечего — реже, но не молча', () {
      final pace = paceFor(watching: 0, power: const PowerState(percent: 80));
      expect(pace.sleep, const Duration(hours: 3));
      // Прогон всё равно идёт: новые идеи ищутся, просто реже.
      expect(pace.skipRun, isFalse);
      expect(pace.reason, contains('открытых сделок нет'));
    });

    test('низкий заряд растягивает шаг', () {
      final pace = paceFor(watching: 2, power: const PowerState(percent: 15));
      expect(pace.sleep, const Duration(hours: 4));
      expect(pace.reason, contains('15%'));
    });

    test('зарядка снимает экономию', () {
      // На зарядке 15% — это не «мало», а «сейчас будет много».
      final pace = paceFor(
        watching: 2,
        power: const PowerState(percent: 15, charging: true),
      );
      expect(pace.sleep, const Duration(hours: 1));
    });

    test('системное энергосбережение — прямое указание владельца', () {
      final pace = paceFor(
        watching: 2,
        power: const PowerState(percent: 90, saver: true),
      );
      expect(pace.sleep, const Duration(hours: 4));
      expect(pace.reason, contains('энергосбережение'));
    });

    test('открытая позиция важнее процентов заряда', () {
      // Незащищённая сделка стоит дороже, чем проценты батареи: экономить
      // можно на поиске новых идей, но не на сопровождении открытой.
      final pace = paceFor(watching: 1, power: const PowerState(percent: 5));
      expect(pace.skipRun, isFalse);
      expect(pace.reason, contains('слежу за позициями'));
    });

    test('заряда почти нет и сторожить нечего — прогон пропускается', () {
      final pace = paceFor(watching: 0, power: const PowerState(percent: 5));
      expect(pace.skipRun, isTrue);
      // Пропуск обязан быть виден: «ничего не делал три часа» без причины
      // неотличимо от «сломался».
      expect(pace.reason, contains('сторожить нечего'));
    });

    test('неизвестный заряд не повод экономить на догадке', () {
      // −1 значит «не смогли прочитать». Считать это нулём — остановить
      // сопровождение по ошибке чтения.
      final pace = paceFor(watching: 1, power: const PowerState());
      expect(pace.sleep, const Duration(hours: 1));
      expect(pace.skipRun, isFalse);
    });
  });

  group('Разбор состояния питания', () {
    test('проценты и питание читаются с платформы', () {
      final power = PowerState.fromMap(
        const {'percent': 42, 'charging': true, 'saver': false},
      );
      expect(power.percent, 42);
      expect(power.charging, isTrue);
      expect(power.low, isFalse);
    });

    test('пустой ответ платформы — «неизвестно», а не «полный»', () {
      final power = PowerState.fromMap(const {});
      expect(power.percent, -1);
      expect(power.low, isFalse);
      expect(power.critical, isFalse);
    });
  });
}
