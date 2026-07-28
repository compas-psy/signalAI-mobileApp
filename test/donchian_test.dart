import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/donchian.dart';

/// Ряд с заданными закрытиями; high/low раздвинуты на [range] от закрытия,
/// чтобы у ATR было ненулевое значение.
List<Candle> series(List<double> closes, {double range = 1}) {
  var date = DateTime.utc(2026, 1, 1);
  return [
    for (final close in closes)
      () {
        final candle = Candle(
          time: date,
          open: close,
          high: close + range,
          low: close - range,
          close: close,
          volume: 100,
        );
        date = date.add(const Duration(days: 1));
        return candle;
      }(),
  ];
}

void main() {
  group('Канал Дончиана', () {
    test('без пробоя сигнала нет', () {
      // Ровный боковик: ни один бар не закрывается выше канала предыдущих.
      final flat = series(List.filled(60, 100));
      expect(Donchian.evaluate(flat), isNull);
    });

    test('короткая история сигнала не даёт, а не выдумывает канал', () {
      expect(Donchian.evaluate(series([100, 101, 102])), isNull);
    });

    test('пробой вверх даёт лонг со стопом в два ATR под уровнем', () {
      // 40 баров около 100, затем закрытие выше максимума канала.
      final closes = <double>[...List.filled(50, 100), 120];
      final signal = Donchian.evaluate(series(closes), entryPeriod: 20);

      expect(signal, isNotNull);
      expect(signal!.long, isTrue);
      // Канал построен по барам ДО пробойного: максимум 100 + range.
      expect(signal.entry, 101);
      expect(signal.stop, lessThan(signal.entry));
      expect(signal.entry - signal.stop, closeTo(2 * signal.atr, 1e-9));
      expect(signal.risk, greaterThan(0));
    });

    test('пробой вниз даёт шорт со стопом над уровнем', () {
      final closes = <double>[...List.filled(50, 100), 80];
      final signal = Donchian.evaluate(series(closes), entryPeriod: 20);

      expect(signal, isNotNull);
      expect(signal!.long, isFalse);
      expect(signal.entry, 99);
      expect(signal.stop, greaterThan(signal.entry));
      expect(signal.stop - signal.entry, closeTo(2 * signal.atr, 1e-9));
    });

    test('бар не сравнивается сам с собой', () {
      // Один растущий ряд: каждый бар обновляет максимум собственным хвостом.
      // Если канал считать включительно, пробоем окажется каждый бар подряд —
      // и система будет входить всегда.
      final rising = series([for (var i = 0; i < 60; i++) 100 + i * 0.01]);
      final signal = Donchian.evaluate(rising, entryPeriod: 20);
      // Медленный дрейф внутри диапазона high/low пробоем считаться не должен.
      expect(signal, isNull);
    });

    test('старый пробой не берётся: цена ушла, стоп остался далеко', () {
      // Пробой случился 10 баров назад — торговать его поздно.
      final closes = <double>[
        ...List.filled(50, 100),
        120,
        ...List.filled(10, 121),
      ];
      final signal = Donchian.evaluate(series(closes), entryPeriod: 20);
      expect(signal, isNull);
    });

    test('свежесть пробоя видна в сигнале', () {
      final closes = <double>[...List.filled(50, 100), 120, 121];
      final signal = Donchian.evaluate(series(closes), entryPeriod: 20);
      expect(signal, isNotNull);
      expect(signal!.barsSinceBreak, lessThanOrEqualTo(2));
    });

    test('медленный канал берёт меньше сделок, чем быстрый', () {
      // Пробой 20-дневного канала, которого не хватает для 55-дневного.
      final closes = <double>[
        ...List.filled(60, 130),
        ...List.filled(30, 100),
        112,
      ];
      final fast = Donchian.evaluate(series(closes),
          entryPeriod: Donchian.fastEntry, exitPeriod: Donchian.fastExit);
      final slow = Donchian.evaluate(series(closes),
          entryPeriod: Donchian.slowEntry, exitPeriod: Donchian.slowExit);

      expect(fast, isNotNull, reason: 'быстрый канал пробит');
      expect(slow, isNull, reason: 'до максимума 55 дней цена не дошла');
    });

    test('уровень выхода — обратный пробой, а не цель', () {
      final closes = <double>[...List.filled(50, 100), 120];
      final signal = Donchian.evaluate(series(closes), entryPeriod: 20)!;
      // Для лонга выход по минимуму последних exitPeriod баров: он ниже входа
      // и служит правилом сопровождения, а не тейк-профитом.
      expect(signal.exit, lessThan(signal.entry));
    });
  });
}
