import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../data/api/engine_client.dart';
import '../../domain/idea/paper_position.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';

/// Журнал thin-клиента: только серверный источник истины.
///
/// Старый JournalScreen строился из локального ledger и поэтому не видел
/// paper-сделки, которые уже сопровождал VPS. Здесь один и тот же PaperTrade
/// отвечает и за «В работе», и за историю: pending/open/closed/cancelled не
/// могут разъехаться по двум хранилищам.
class ServerJournalScreen extends StatefulWidget {
  const ServerJournalScreen({super.key, required this.pill});

  final int pill;

  @override
  State<ServerJournalScreen> createState() => _ServerJournalScreenState();
}

class _ServerJournalScreenState extends State<ServerJournalScreen> {
  final EngineClient _engine = EngineClient();
  final ApiClient _api = ApiClient();
  List<PaperPosition>? _trades;
  List<_Skip>? _skips;
  String? _error;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final trades = await _engine.paperTrades(liveOnly: false);
      final skipRaw = await _api.getList('/api/v1/journal/skips?limit=100');
      if (!mounted) return;
      setState(() {
        _trades = trades ?? const [];
        _skips = [
          for (final raw in skipRaw)
            if (raw is Map<String, dynamic>) _Skip.fromJson(raw),
        ];
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final section = JournalPill.values[
      widget.pill.clamp(0, JournalPill.values.length - 1),
    ];
    if (_error != null && _trades == null) {
      return _Failure(error: _error!, onRetry: _reload);
    }
    if (_trades == null) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(28),
          child: BusyLine(label: 'Читаем серверный журнал…'),
        ),
      );
    }
    return switch (section) {
      JournalPill.trades => _Trades(
          trades: _trades!,
          loading: _loading,
          onReload: _reload,
        ),
      JournalPill.skips => _Skips(
          rows: _skips ?? const [],
          loading: _loading,
          onReload: _reload,
        ),
      JournalPill.metrics => _Metrics(
          trades: _trades!,
          loading: _loading,
          onReload: _reload,
        ),
    };
  }
}

class _Trades extends StatelessWidget {
  const _Trades({
    required this.trades,
    required this.loading,
    required this.onReload,
  });

  final List<PaperPosition> trades;
  final bool loading;
  final Future<void> Function() onReload;

  @override
  Widget build(BuildContext context) {
    final live = trades.where((t) => t.status.live).toList();
    final history = trades.where((t) => !t.status.live).toList();
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _JournalStatus(
          total: trades.length,
          live: live.length,
          loading: loading,
          onReload: onReload,
        ),
        const SizedBox(height: 12),
        SectionLabel('Сейчас ведёт сервер · ${live.length}'),
        const SizedBox(height: 8),
        if (live.isEmpty)
          const _EmptyCard(
            title: 'Активных paper-сделок нет',
            note: 'Подтверждённая идея появится здесь сразу после создания '
                'серверной сделки — ещё до исполнения входа.',
          )
        else
          for (final trade in live) ...[
            _TradeCard(trade: trade),
            const SizedBox(height: 10),
          ],
        const SizedBox(height: 4),
        SectionLabel('История · ${history.length}'),
        const SizedBox(height: 8),
        if (history.isEmpty)
          const _EmptyCard(
            title: 'Закрытых сделок пока нет',
            note: 'CLOSED и CANCELLED останутся здесь после завершения — '
                'серверная история не зависит от того, был ли открыт телефон.',
          )
        else
          for (final trade in history) ...[
            _TradeCard(trade: trade),
            const SizedBox(height: 10),
          ],
      ],
    );
  }
}

class _TradeCard extends StatelessWidget {
  const _TradeCard({required this.trade});

  final PaperPosition trade;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final live = trade.status.live;
    final statusColor = switch (trade.status) {
      PaperPositionStatus.open => C.green,
      PaperPositionStatus.pending => C.accent,
      PaperPositionStatus.closed => (trade.resultR ?? 0) >= 0 ? C.green : C.red,
      PaperPositionStatus.cancelled => C.muted,
    };
    return Pressable(
      onTap: trade.ideaId.isEmpty
          ? null
          : () => controller.openSignal(trade.ideaId),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text(trade.symbol, style: T.jost(16))),
                Text(
                  trade.long ? 'LONG' : 'SHORT',
                  style: T.mono(10, weight: 700, color: trade.long ? C.green : C.red),
                ),
                const SizedBox(width: 8),
                OutlineBadge(
                  label: _status(trade.status),
                  color: statusColor,
                  borderColor: statusColor,
                  fontWeight: 700,
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              switch (trade.status) {
                PaperPositionStatus.pending =>
                  'Заявка ждёт вход ${_price(trade.entry)}. Сервер продолжает наблюдение.',
                PaperPositionStatus.open =>
                  'Позиция открыта. Взято целей ${trade.tpsTaken} из ${trade.tpsTotal}.',
                PaperPositionStatus.closed =>
                  trade.closeReason.isEmpty ? 'Сделка закрыта.' : trade.closeReason,
                PaperPositionStatus.cancelled =>
                  trade.closeReason.isEmpty ? 'Заявка отменена.' : trade.closeReason,
              },
              style: T.body(11.5, color: C.textSecondary, height: 1.45),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                TagChip('вход ${_price(trade.entry)}'),
                TagChip('стоп ${_price(trade.currentStop)}'),
                if (trade.atBreakeven) const TagChip('стоп в БУ'),
                if (trade.resultR != null)
                  TagChip('${trade.resultRealized ? 'зафикс.' : 'результат'} ${_r(trade.resultR!)}'),
                if (trade.stale) TagChip('сверка молчит ${trade.staleHours} ч'),
              ],
            ),
            if (trade.lastReconciledAt != null) ...[
              const SizedBox(height: 7),
              Text(
                'последняя сверка ${_moment(trade.lastReconciledAt!)}',
                style: T.mono(9.5, color: trade.stale ? C.warning : C.faint),
              ),
            ],
            if (!live && trade.closedAt != null) ...[
              const SizedBox(height: 4),
              Text('закрыта ${_moment(trade.closedAt!)}',
                  style: T.mono(9.5, color: C.faint)),
            ],
          ],
        ),
      ),
    );
  }
}

class _Skips extends StatelessWidget {
  const _Skips({required this.rows, required this.loading, required this.onReload});

  final List<_Skip> rows;
  final bool loading;
  final Future<void> Function() onReload;

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
        children: [
          _JournalStatus(
            total: rows.length,
            live: 0,
            loading: loading,
            onReload: onReload,
            label: 'решений об отказе',
          ),
          const SizedBox(height: 12),
          if (rows.isEmpty)
            const _EmptyCard(
              title: 'Отказов пока нет',
              note: 'Если идея отклонена, решение и причина остаются на VPS. '
                  'Повторный запуск приложения их не стирает.',
            )
          else
            for (final row in rows) ...[
              Pressable(
                onTap: () => AppScope.of(context).openSignal(row.ideaId),
                child: SectionCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Expanded(child: Text(row.symbol, style: T.jost(15))),
                          Text(row.direction, style: T.mono(10, color: C.muted)),
                          const SizedBox(width: 8),
                          Text(row.score.toStringAsFixed(0),
                              style: T.mono(11, color: C.accent)),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(row.reason, style: T.body(11.5, weight: 700)),
                      if (row.comment.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text(row.comment,
                            style: T.body(11, color: C.muted, height: 1.4)),
                      ],
                      const SizedBox(height: 6),
                      Text(_moment(row.at), style: T.mono(9.5, color: C.faint)),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 10),
            ],
        ],
      );
}

class _Metrics extends StatelessWidget {
  const _Metrics({required this.trades, required this.loading, required this.onReload});

  final List<PaperPosition> trades;
  final bool loading;
  final Future<void> Function() onReload;

  @override
  Widget build(BuildContext context) {
    final closed = trades
        .where((t) => t.status == PaperPositionStatus.closed && t.resultR != null)
        .toList();
    final results = [for (final t in closed) t.resultR!];
    final wins = results.where((r) => r > 0).length;
    final sum = results.fold<double>(0, (a, b) => a + b);
    final avg = results.isEmpty ? null : sum / results.length;
    final winRate = results.isEmpty ? null : wins / results.length;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _JournalStatus(
          total: trades.length,
          live: trades.where((t) => t.status.live).length,
          loading: loading,
          onReload: onReload,
        ),
        const SizedBox(height: 12),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Paper · фактическая выборка'),
              const SizedBox(height: 10),
              _Metric('Закрыто', '${closed.length}'),
              _Metric('Сумма R', results.isEmpty ? '—' : _r(sum)),
              _Metric('Среднее R', avg == null ? '—' : _r(avg)),
              _Metric('Винрейт', winRate == null ? '—' : '${(winRate * 100).round()}%'),
              const SizedBox(height: 8),
              Text(
                closed.length < 30
                    ? 'Выборка пока мала: эти числа нужны для контроля '
                        'исполнения, а не для выводов о качестве стратегии.'
                    : 'Метрики посчитаны только по закрытым серверным '
                        'paper-сделкам. Pending и Open результат не подменяют.',
                style: T.body(10.5, color: C.muted, height: 1.45),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.name, this.value);
  final String name;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(
          children: [
            Expanded(child: Text(name, style: T.body(12, color: C.muted))),
            Text(value, style: T.mono(13, weight: 700)),
          ],
        ),
      );
}

class _JournalStatus extends StatelessWidget {
  const _JournalStatus({
    required this.total,
    required this.live,
    required this.loading,
    required this.onReload,
    this.label = 'сделок в ledger',
  });

  final int total;
  final int live;
  final bool loading;
  final Future<void> Function() onReload;
  final String label;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Источник истины · VPS')),
                OutlineBadge(
                  label: 'SERVER',
                  color: C.green,
                  borderColor: C.greenBorder,
                  fontWeight: 700,
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text('$total $label${live > 0 ? ' · сейчас ведётся $live' : ''}',
                style: T.body(11.5, color: C.textSecondary)),
            const SizedBox(height: 9),
            if (loading)
              const BusyLine(label: 'Обновляем журнал…')
            else
              ActionButton(label: 'Обновить', dense: true, onTap: onReload),
          ],
        ),
      );
}

class _EmptyCard extends StatelessWidget {
  const _EmptyCard({required this.title, required this.note});
  final String title;
  final String note;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: T.body(13, weight: 700)),
            const SizedBox(height: 5),
            Text(note, style: T.body(11, color: C.muted, height: 1.45)),
          ],
        ),
      );
}

class _Failure extends StatelessWidget {
  const _Failure({required this.error, required this.onRetry});
  final String error;
  final Future<void> Function() onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: SectionCard(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Журнал не прочитан', style: T.jost(17)),
                const SizedBox(height: 6),
                Text(error, style: T.body(11, color: C.warning, height: 1.45)),
                const SizedBox(height: 10),
                ActionButton(label: 'Повторить', onTap: onRetry),
              ],
            ),
          ),
        ),
      );
}

class _Skip {
  const _Skip({
    required this.ideaId,
    required this.symbol,
    required this.direction,
    required this.score,
    required this.reason,
    required this.comment,
    required this.at,
  });

  final String ideaId;
  final String symbol;
  final String direction;
  final double score;
  final String reason;
  final String comment;
  final DateTime at;

  factory _Skip.fromJson(Map<String, dynamic> json) => _Skip(
        ideaId: '${json['idea_id'] ?? ''}',
        symbol: '${json['symbol'] ?? ''}',
        direction: '${json['direction'] ?? ''}',
        score: double.tryParse('${json['score'] ?? 0}') ?? 0,
        reason: '${json['reason'] ?? ''}',
        comment: '${json['comment'] ?? ''}',
        at: DateTime.tryParse('${json['skipped_at'] ?? ''}')?.toLocal() ?? DateTime.now(),
      );
}

String _status(PaperPositionStatus value) => switch (value) {
      PaperPositionStatus.pending => 'ЖДЁТ ВХОД',
      PaperPositionStatus.open => 'ОТКРЫТА',
      PaperPositionStatus.closed => 'ЗАКРЫТА',
      PaperPositionStatus.cancelled => 'ОТМЕНЕНА',
    };

String _price(double value) => value.toStringAsFixed(value.abs() < 0.01 ? 6 : 4)
    .replaceAll(RegExp(r'0+$'), '')
    .replaceAll(RegExp(r'\.$'), '')
    .replaceAll('.', ',');

String _r(double value) =>
    '${value >= 0 ? '+' : '−'}${value.abs().toStringAsFixed(2).replaceAll('.', ',')}R';

String _moment(DateTime value) {
  final v = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(v.day)}.${two(v.month)} ${two(v.hour)}:${two(v.minute)}';
}
