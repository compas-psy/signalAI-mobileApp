import '../ledger/signal_ledger.dart';

/// Разрез статистики: срез выборки и его показатели.
class MetricSlice {
  const MetricSlice({
    required this.name,
    required this.trades,
    required this.wins,
    required this.grossWinR,
    required this.grossLossR,
    required this.maxDrawdownR,
  });

  final String name;
  final int trades;
  final int wins;

  /// Сумма положительных результатов, R.
  final double grossWinR;

  /// Сумма отрицательных результатов по модулю, R.
  final double grossLossR;

  /// Максимальная просадка кривой в R.
  final double maxDrawdownR;

  int get losses => trades - wins;

  double get netR => grossWinR - grossLossR;

  /// Матожидание на сделку, R. Главное число: положительный profit factor
  /// при мизерной выборке ещё ничего не значит, а expectancy хотя бы
  /// отвечает «сколько это приносит за раз».
  double? get expectancyR => trades == 0 ? null : netR / trades;

  /// Profit factor. null — не было ни одного убытка: делить не на что, и
  /// показывать «бесконечность» как достижение нельзя.
  double? get profitFactor =>
      grossLossR <= 0 ? null : grossWinR / grossLossR;

  double? get winRate => trades == 0 ? null : wins / trades;

  double? get averageWinR => wins == 0 ? null : grossWinR / wins;

  double? get averageLossR => losses == 0 ? null : grossLossR / losses;

  /// Достаточна ли выборка для выводов.
  ///
  /// ТЗ §22 требует 200–300 сделок на класс стратегии до статистических
  /// выводов. Ниже этого числа показатели показываются, но с оговоркой —
  /// прятать их значило бы лишить владельца единственной обратной связи,
  /// а выдавать за истину — обманывать.
  bool get conclusive => trades >= minimumForConclusions;

  static const minimumForConclusions = 200;
}

/// Статистика журнала (ТЗ §12.1).
///
/// Считается в R, а не в рублях: риск на сделку меняется вместе с капиталом
/// и оценкой, поэтому рублёвые суммы разных периодов несопоставимы, а R —
/// сопоставим. Перевод в рубли делается на выходе через текущий риск.
class JournalMetrics {
  const JournalMetrics({
    required this.overall,
    required this.byStrategy,
    required this.byScoreBucket,
  });

  final MetricSlice overall;

  /// Разрез по стратегиям.
  final List<MetricSlice> byStrategy;

  /// Разрез по диапазонам оценки — основа калибровки §22.1.
  final List<MetricSlice> byScoreBucket;

  /// Монотонна ли калибровка: высокие оценки не хуже низких.
  ///
  /// Требование live gate §22.1. null — сравнивать нечего: меньше двух
  /// непустых корзин.
  bool? get calibrationMonotonic {
    final filled = byScoreBucket
        .where((s) => s.trades > 0 && s.expectancyR != null)
        .toList();
    if (filled.length < 2) return null;
    for (var i = 1; i < filled.length; i++) {
      if (filled[i].expectancyR! < filled[i - 1].expectancyR!) return false;
    }
    return true;
  }

  /// Границы корзин оценки — те же, что в таблице риска ТЗ §14.2.
  static const scoreBuckets = <(String, int, int)>[
    ('65–74', 65, 75),
    ('75–79', 75, 80),
    ('80–89', 80, 90),
    ('90–100', 90, 101),
  ];

  static JournalMetrics fromLedger(SignalLedger ledger) {
    final closed = [...ledger.closed]..sort((a, b) =>
        (a.closedAt ?? a.createdAt).compareTo(b.closedAt ?? b.createdAt));

    MetricSlice slice(String name, List<PaperTrade> trades) {
      var wins = 0;
      var win = 0.0;
      var loss = 0.0;
      var equity = 0.0;
      var peak = 0.0;
      var drawdown = 0.0;
      for (final trade in trades) {
        final r = trade.resultR ?? 0;
        if (r > 0) {
          wins++;
          win += r;
        } else {
          loss += -r;
        }
        equity += r;
        if (equity > peak) peak = equity;
        final gap = peak - equity;
        if (gap > drawdown) drawdown = gap;
      }
      return MetricSlice(
        name: name,
        trades: trades.length,
        wins: wins,
        grossWinR: win,
        grossLossR: loss,
        maxDrawdownR: drawdown,
      );
    }

    final byStrategy = <String, List<PaperTrade>>{};
    for (final trade in closed) {
      byStrategy.putIfAbsent(trade.strategyId, () => []).add(trade);
    }

    return JournalMetrics(
      overall: slice('Всего', closed),
      byStrategy: [
        for (final entry in byStrategy.entries) slice(entry.key, entry.value),
      ]..sort((a, b) => b.trades.compareTo(a.trades)),
      byScoreBucket: [
        for (final (label, from, to) in scoreBuckets)
          slice(
            label,
            [
              for (final t in closed)
                if (t.score >= from && t.score < to) t,
            ],
          ),
      ],
    );
  }
}
