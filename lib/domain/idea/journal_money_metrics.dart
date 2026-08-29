import 'paper_position.dart';

/// Денежная фактическая выборка одной валюты.
///
/// RUB и USDT принципиально не складываются. Для сравнения стратегий R остаётся
/// отдельной нормализованной метрикой; здесь владелец видит именно деньги.
class JournalMoneyMetric {
  const JournalMoneyMetric({
    required this.currency,
    required this.count,
    required this.sum,
  });

  final String currency;
  final int count;
  final double sum;

  double get average => count == 0 ? 0 : sum / count;
}

List<JournalMoneyMetric> journalMoneyMetrics(
  Iterable<PaperPosition> trades,
) {
  final sums = <String, double>{};
  final counts = <String, int>{};

  for (final trade in trades) {
    if (trade.status != PaperPositionStatus.closed) continue;
    final pnl = trade.realizedPnl;
    final currency = trade.pnlCurrency?.trim().toUpperCase();
    if (pnl == null || currency == null || currency.isEmpty) continue;
    sums[currency] = (sums[currency] ?? 0) + pnl;
    counts[currency] = (counts[currency] ?? 0) + 1;
  }

  final currencies = sums.keys.toList()
    ..sort((a, b) {
      const preferred = {'RUB': 0, 'USDT': 1};
      final ai = preferred[a] ?? 100;
      final bi = preferred[b] ?? 100;
      if (ai != bi) return ai.compareTo(bi);
      return a.compareTo(b);
    });

  return List<JournalMoneyMetric>.unmodifiable([
    for (final currency in currencies)
      JournalMoneyMetric(
        currency: currency,
        count: counts[currency]!,
        sum: sums[currency]!,
      ),
  ]);
}
