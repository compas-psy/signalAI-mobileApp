import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/journal_metrics.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';

final start = DateTime.utc(2026, 7, 1);

PaperTrade closed(
  double r, {
  int score = 80,
  String strategy = 'forts',
  int day = 1,
}) =>
    PaperTrade(
      id: 'trade_${day}_$r',
      symbol: 'SiZ6',
      strategyId: strategy,
      long: true,
      entry: 100,
      stopLoss: 96,
      tpPrices: const [108],
      tpShares: const [100],
      score: score,
      createdAt: start.add(Duration(days: day)),
      closedAt: start.add(Duration(days: day, hours: 6)),
      status: PaperStatus.closed,
      resultR: r,
    );

JournalMetrics of(List<PaperTrade> trades) =>
    JournalMetrics.fromLedger(SignalLedger(trades: trades, rejected: const []));

void main() {
  group('Показатели журнала (ТЗ §12.1)', () {
    test('пустой журнал не выдаёт чисел', () {
      // Ноль на месте матожидания читается как «стратегия в нуле», хотя
      // сделок не было вовсе. Это разные вещи.
      final m = of(const []);
      expect(m.overall.trades, 0);
      expect(m.overall.expectancyR, isNull);
      expect(m.overall.winRate, isNull);
      expect(m.overall.profitFactor, isNull);
    });

    test('матожидание и profit factor считаются по закрытым сделкам', () {
      final m = of([
        closed(2, day: 1),
        closed(-1, day: 2),
        closed(3, day: 3),
        closed(-1, day: 4),
      ]);
      expect(m.overall.trades, 4);
      expect(m.overall.wins, 2);
      expect(m.overall.netR, closeTo(3.0, 1e-9));
      expect(m.overall.expectancyR, closeTo(0.75, 1e-9));
      expect(m.overall.profitFactor, closeTo(2.5, 1e-9));
      expect(m.overall.winRate, closeTo(0.5, 1e-9));
      expect(m.overall.averageWinR, closeTo(2.5, 1e-9));
      expect(m.overall.averageLossR, closeTo(1.0, 1e-9));
    });

    test('серия без убытков не выдаёт бесконечный profit factor', () {
      // Делить не на что. «∞» в графе «профит-фактор» выглядит достижением,
      // а означает всего лишь «убытков ещё не было».
      final m = of([closed(2, day: 1), closed(1, day: 2)]);
      expect(m.overall.profitFactor, isNull);
      expect(m.overall.expectancyR, closeTo(1.5, 1e-9));
    });

    test('просадка считается по кривой, а не по худшей сделке', () {
      // +3, −1, −1, +2: пик 3, дно 1, просадка 2 — при том что худшая
      // сделка всего −1.
      final m = of([
        closed(3, day: 1),
        closed(-1, day: 2),
        closed(-1, day: 3),
        closed(2, day: 4),
      ]);
      expect(m.overall.maxDrawdownR, closeTo(2.0, 1e-9));
    });

    test('порядок сделок берётся по времени закрытия', () {
      // Журнал может лежать в любом порядке; просадка от порядка зависит.
      final m = of([
        closed(-1, day: 3),
        closed(3, day: 1),
        closed(-1, day: 2),
      ]);
      expect(m.overall.maxDrawdownR, closeTo(2.0, 1e-9));
    });

    test('выборка ниже порога помечена как недостаточная', () {
      // ТЗ §22: 200–300 сделок на класс до статистических выводов.
      expect(of([closed(1, day: 1)]).overall.conclusive, isFalse);
      expect(MetricSlice.minimumForConclusions, 200);
    });
  });

  group('Разрезы', () {
    test('по стратегиям, от больших выборок к меньшим', () {
      final m = of([
        closed(1, strategy: 'forts', day: 1),
        closed(-1, strategy: 'forts', day: 2),
        closed(2, strategy: 'crypto', day: 3),
      ]);
      expect(m.byStrategy.first.name, 'forts');
      expect(m.byStrategy.first.trades, 2);
      expect(m.byStrategy[1].name, 'crypto');
    });

    test('корзины оценки совпадают с таблицей риска §14.2', () {
      expect(JournalMetrics.scoreBuckets.map((b) => b.$1).toList(),
          ['65–74', '75–79', '80–89', '90–100']);
      final m = of([
        closed(1, score: 70, day: 1),
        closed(1, score: 78, day: 2),
        closed(1, score: 85, day: 3),
        closed(1, score: 95, day: 4),
      ]);
      for (final bucket in m.byScoreBucket) {
        expect(bucket.trades, 1, reason: bucket.name);
      }
    });

    test('калибровка признаётся ломаной, когда высокие оценки хуже низких', () {
      // ТЗ §22.1: высшие корзины не должны быть хуже низших. Если это не
      // так, оценка не работает — и молчать об этом нельзя.
      final broken = of([
        closed(2, score: 70, day: 1),
        closed(-1, score: 95, day: 2),
      ]);
      expect(broken.calibrationMonotonic, isFalse);

      final good = of([
        closed(-1, score: 70, day: 1),
        closed(2, score: 95, day: 2),
      ]);
      expect(good.calibrationMonotonic, isTrue);
    });

    test('одной корзины мало для суждения о калибровке', () {
      final m = of([closed(1, score: 85, day: 1)]);
      expect(m.calibrationMonotonic, isNull);
    });
  });
}
