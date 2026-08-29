import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/journal_money_metrics.dart';
import 'package:signalai/domain/idea/paper_position.dart';

PaperPosition _trade({
  required String id,
  required double pnl,
  required String currency,
}) =>
    PaperPosition(
      id: id,
      symbol: 'TEST',
      long: true,
      pending: false,
      status: PaperPositionStatus.closed,
      entry: 100,
      initialStop: 90,
      currentStop: 90,
      tpPrices: const [110],
      tpsTaken: 1,
      resultR: pnl >= 0 ? 1 : -1,
      resultRealized: true,
      realizedPnl: pnl,
      pnlCurrency: currency,
      fromServer: true,
    );

void main() {
  test('aggregates RUB and USDT independently', () {
    final metrics = journalMoneyMetrics([
      _trade(id: 'r1', pnl: 1000, currency: 'RUB'),
      _trade(id: 'r2', pnl: -250, currency: 'rub'),
      _trade(id: 'u1', pnl: 12.5, currency: 'USDT'),
      _trade(id: 'u2', pnl: 7.5, currency: 'USDT'),
    ]);

    expect(metrics, hasLength(2));
    final rub = metrics.singleWhere((m) => m.currency == 'RUB');
    final usdt = metrics.singleWhere((m) => m.currency == 'USDT');

    expect(rub.count, 2);
    expect(rub.sum, 750);
    expect(rub.average, 375);
    expect(usdt.count, 2);
    expect(usdt.sum, 20);
    expect(usdt.average, 10);
  });

  test('ignores missing money instead of turning it into zero', () {
    final missing = PaperPosition(
      id: 'old',
      symbol: 'OLD',
      long: true,
      pending: false,
      status: PaperPositionStatus.closed,
      entry: 100,
      initialStop: 90,
      currentStop: 90,
      tpPrices: const [110],
      tpsTaken: 0,
      resultR: -1,
      resultRealized: true,
      fromServer: true,
    );

    expect(journalMoneyMetrics([missing]), isEmpty);
  });

  test('does not include open partial realized R in monetary sample', () {
    final open = PaperPosition(
      id: 'open',
      symbol: 'OPEN',
      long: true,
      pending: false,
      status: PaperPositionStatus.open,
      entry: 100,
      initialStop: 90,
      currentStop: 100,
      tpPrices: const [110],
      tpsTaken: 1,
      resultR: 0.3,
      resultRealized: true,
      realizedPnl: 300,
      pnlCurrency: 'RUB',
      fromServer: true,
    );

    expect(journalMoneyMetrics([open]), isEmpty);
  });
}