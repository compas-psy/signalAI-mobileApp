import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';
import 'package:signalai/domain/models/portfolio.dart';

/// Сделка помнит идею, из которой родилась.
///
/// Без этой ссылки журнал — список тикеров: увидев сделку, нельзя посмотреть,
/// на чём она строилась, а именно за этим в журнал и приходят.
void main() {
  test('открытая сделка запоминает идею', () {
    final ledger = SignalLedger();
    ledger.record(_signal('sig-mx-1', 'MXU6'), DateTime(2026, 7, 31, 10));

    expect(ledger.trades, hasLength(1));
    expect(ledger.trades.single.signalId, 'sig-mx-1');
    // Собственный идентификатор сделки остаётся своим: он про сделку, а
    // ссылка — про идею. Смешать их значит потерять одну из связей.
    expect(ledger.trades.single.id, isNot('sig-mx-1'));
  });

  test('ссылка переживает запись на диск', () {
    final ledger = SignalLedger();
    ledger.record(_signal('sig-2', 'SIU6'), DateTime(2026, 7, 31));
    final restored = PaperTrade.fromJson(ledger.trades.single.toJson());
    expect(restored.signalId, 'sig-2');
  });

  test('сделка без ссылки не притворяется связанной', () {
    // Записи, сделанные до появления ссылки, задним числом её не получают:
    // журнал только дополняется, переписывать его нельзя.
    final old = PaperTrade.fromJson({
      'id': 'MXU6-1',
      'symbol': 'MXU6',
      'strategy_id': 'forts',
      'long': true,
      'entry': 100.0,
      'stop_loss': 95.0,
      'tp_prices': <double>[110.0],
      'tp_shares': <int>[100],
      'score': 80,
      'created_at': '2026-07-01T00:00:00.000',
      'status': 'closed',
    });
    expect(old.signalId, isEmpty);
  });

  test('строка журнала и позиция несут ссылку дальше', () {
    const position = ActivePosition(
      signalId: 'sig-3',
      symbol: 'MXU6',
      direction: Direction.long,
      entryLabel: '100',
      currentLabel: 'в позиции',
      pnlLabel: '+1,2R',
      pnlPositive: true,
      progressPercent: 30,
      stage: 'бумажная',
    );
    const entry = JournalEntry(
      signalId: 'sig-3',
      date: '31.07',
      symbol: 'MXU6',
      direction: Direction.long,
      outcome: 'TP1',
      rMultiple: 1.2,
    );
    expect(position.signalId, 'sig-3');
    expect(entry.signalId, 'sig-3');
  });
}

TradingSignal _signal(String id, String symbol) => TradingSignal(
      id: id,
      symbol: symbol,
      name: symbol,
      market: Market.forts,
      direction: Direction.long,
      horizon: Horizon.swing,
      horizonLabel: '1d',
      score: 80,
      entry: 100,
      stopLoss: 95,
      takeProfits: const [TakeProfit(index: 1, price: 110, sharePercent: 100)],
      priceDecimals: 0,
      riskReward: '2,0',
      chips: const [],
      note: '',
      factors: const [],
      events: const [],
      unitRisk: 5,
      unitRiskLabel: '',
      unitMultiplier: 1,
      unitDecimals: 0,
      unitName: '',
      lastPrice: '',
      changeLabel: '',
      changeUp: true,
      status: SignalStatus.proposed,
      validUntil: DateTime(2026, 8, 5),
      strategyId: 'forts',
    );
