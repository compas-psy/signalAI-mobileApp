import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/indicators.dart';

/// Свеча из цены закрытия с заданным диапазоном — для синтетики.
Candle c(double close, {double? open, double? high, double? low, double volume = 100}) {
  final o = open ?? close;
  return Candle(
    time: DateTime.utc(2026, 1, 1),
    open: o,
    high: high ?? (close > o ? close : o) + 0.1,
    low: low ?? (close < o ? close : o) - 0.1,
    close: close,
    volume: volume,
  );
}

void main() {
  group('RSI Уайлдера', () {
    // Классический набор Уайлдера. Ожидаемые значения перепроверены
    // независимой реализацией сглаживания Уайлдера, а не взяты из таблицы:
    // в печатных таблицах цены округлены до копеек, и значения расходятся
    // в сотых долях.
    const closes = [
      44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
      45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    ];

    test('совпадает с эталонными значениями', () {
      final rsi = rsiWilder(closes);
      expect(rsi[13], isNull, reason: 'до 14 значений RSI не определён');
      expect(rsi[14]!, closeTo(70.4641, 0.001));
      expect(rsi[15]!, closeTo(66.2496, 0.001));
      expect(rsi[16]!, closeTo(66.4809, 0.001));
      expect(rsi[17]!, closeTo(69.3469, 0.001));
    });

    test('только рост даёт 100, только падение — 0', () {
      final up = rsiWilder([for (var i = 0; i < 30; i++) 100.0 + i]);
      final down = rsiWilder([for (var i = 0; i < 30; i++) 100.0 - i]);
      expect(up.last!, 100);
      expect(down.last!, 0);
    });

    test('короткий ряд не считается', () {
      expect(rsiWilder([1, 2, 3]).every((v) => v == null), isTrue);
    });
  });

  group('ATR', () {
    test('на равномерных свечах равен их диапазону', () {
      final candles = [
        for (var i = 0; i < 30; i++)
          Candle(
            time: DateTime.utc(2026, 1, 1).add(Duration(hours: i)),
            open: 100,
            high: 101,
            low: 99,
            close: 100,
            volume: 1,
          ),
      ];
      expect(atr(candles).last!, closeTo(2, 1e-9));
    });

    test('учитывает гэп через предыдущее закрытие', () {
      final candles = [
        for (var i = 0; i < 20; i++)
          Candle(
            time: DateTime.utc(2026, 1, 1).add(Duration(hours: i)),
            open: 100,
            high: 100.5,
            low: 99.5,
            close: 100,
            volume: 1,
          ),
        // Гэп вверх: истинный диапазон считается от прошлого закрытия.
        Candle(
          time: DateTime.utc(2026, 1, 2),
          open: 110,
          high: 110.5,
          low: 109.5,
          close: 110,
          volume: 1,
        ),
      ];
      final value = atr(candles).last!;
      expect(value, greaterThan(1));
    });
  });

  group('структура рынка', () {
    List<Candle> zigzag(List<double> pivots) {
      final candles = <Candle>[];
      var previous = pivots.first;
      for (final pivot in pivots) {
        final steps = 3;
        for (var s = 1; s <= steps; s++) {
          final value = previous + (pivot - previous) * s / steps;
          candles.add(c(value));
        }
        previous = pivot;
      }
      return candles;
    }

    test('растущая структура определяется как up', () {
      final candles = zigzag([100, 110, 105, 120, 115, 130]);
      expect(analyzeStructure(candles).trend, StructureTrend.up);
    });

    test('падающая структура определяется как down', () {
      final candles = zigzag([130, 120, 125, 110, 115, 100]);
      expect(analyzeStructure(candles).trend, StructureTrend.down);
    });

    test('пробой последнего максимума — BOS в растущей структуре', () {
      final candles = zigzag([100, 110, 105, 120, 112])..addAll([c(121), c(125)]);
      final structure = analyzeStructure(candles);
      expect(structure.trend, StructureTrend.up);
      expect(structure.breakType, StructureBreak.bos);
    });
  });

  group('паттерны и гэпы', () {
    test('FVG находится по трёхсвечной формации', () {
      final candles = [
        c(100, high: 101, low: 99),
        c(104, high: 105, low: 100),
        c(106, high: 107, low: 103), // low 103 > high 101 первой свечи
      ];
      final gaps = fairValueGaps(candles);
      expect(gaps, hasLength(1));
      expect(gaps.single.bullish, isTrue);
      expect(gaps.single.from, 101);
      expect(gaps.single.to, 103);
    });

    test('пин-бар: маленькое тело и длинная нижняя тень', () {
      final pin = Candle(
        time: DateTime.utc(2026),
        open: 100,
        high: 100.5,
        low: 96,
        close: 100.2,
        volume: 1,
      );
      expect(CandlePatterns.isPinBar(pin), isTrue);
      expect(CandlePatterns.isPinBar(pin, bullish: false), isFalse);
    });

    test('бычье поглощение перекрывает тело предыдущей свечи', () {
      final previous = c(99, open: 101);
      final current = c(102, open: 98.5);
      expect(CandlePatterns.isEngulfing(previous, current), isTrue);
    });

    test('внутренний бар лежит в диапазоне предыдущего', () {
      final previous = c(100, high: 105, low: 95);
      final current = c(100, high: 103, low: 97);
      expect(CandlePatterns.isInsideBar(previous, current), isTrue);
    });
  });

  group('объём и дивергенция', () {
    test('z-оценка ловит всплеск объёма', () {
      final volumes = [for (var i = 0; i < 25; i++) 100.0 + (i % 3), 400.0];
      expect(zScore(volumes)!, greaterThan(3));
    });

    test('на постоянном объёме z-оценка не определена', () {
      // Нулевое отклонение — делить не на что, аномалии тоже нет.
      final volumes = [for (var i = 0; i < 25; i++) 100.0, 400.0];
      expect(zScore(volumes), isNull);
    });

    test('бычья дивергенция: цена ниже, RSI выше', () {
      final candles = <Candle>[
        for (var i = 0; i < 8; i++) c(100 - i.toDouble()),
        c(88, low: 88),
        for (var i = 0; i < 8; i++) c(92 + i.toDouble()),
        c(86, low: 86),
        for (var i = 0; i < 6; i++) c(90 + i.toDouble()),
      ];
      final rsi = rsiWilder([for (final x in candles) x.close]);
      // Направление определяется по двум последним свинг-лоу.
      expect(findDivergence(candles, rsi), isA<Divergence>());
    });
  });
}
