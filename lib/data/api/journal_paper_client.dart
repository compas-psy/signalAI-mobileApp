import '../../domain/idea/paper_position.dart';
import 'api_client.dart';
import 'engine_contract.dart';

/// Read-only Journal adapter for server paper trades.
///
/// The shared EngineContract deliberately stays focused on normalized trading
/// state. Journal additionally needs owner-facing money, so it enriches the
/// same server rows with proven quote-currency P&L without teaching every
/// screen about RUB/USDT accounting.
class JournalPaperClient {
  JournalPaperClient({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  Future<List<PaperPosition>> trades({int limit = 100}) async {
    final raw = await _api.getList('/api/v1/paper/trades?limit=$limit');
    final parsed = EngineContract.paperTrades(raw);
    final moneyById = <String, _Money>{};

    for (final item in raw) {
      if (item is! Map<String, dynamic>) continue;
      final id = '${item['id'] ?? ''}';
      final pnl = _number(item['realized_pnl']);
      final currency = '${item['pnl_currency'] ?? ''}'.trim().toUpperCase();
      if (id.isEmpty || pnl == null || currency.isEmpty) continue;
      moneyById[id] = _Money(pnl, currency);
    }

    return List<PaperPosition>.unmodifiable([
      for (final trade in parsed) _enrich(trade, moneyById[trade.id]),
    ]);
  }

  PaperPosition _enrich(PaperPosition trade, _Money? money) => PaperPosition(
        id: trade.id,
        ideaId: trade.ideaId,
        instrumentId: trade.instrumentId,
        symbol: trade.symbol,
        long: trade.long,
        pending: trade.pending,
        status: trade.status,
        entry: trade.entry,
        initialStop: trade.initialStop,
        currentStop: trade.currentStop,
        tpPrices: trade.tpPrices,
        tpsTaken: trade.tpsTaken,
        breakevenAt: trade.breakevenAt,
        atBreakeven: trade.atBreakeven,
        resultR: trade.resultR,
        resultRealized: trade.resultRealized,
        realizedPnl: money?.value,
        pnlCurrency: money?.currency,
        lastReconciledAt: trade.lastReconciledAt,
        staleHours: trade.staleHours,
        fromServer: trade.fromServer,
        closedAt: trade.closedAt,
        outcome: trade.outcome,
        closeReason: trade.closeReason,
      );

  static double? _number(Object? value) => switch (value) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };
}

class _Money {
  const _Money(this.value, this.currency);

  final double value;
  final String currency;
}
