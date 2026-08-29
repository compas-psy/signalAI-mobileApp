import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../data/api/journal_paper_client.dart';
import '../../domain/idea/journal_money_metrics.dart';
import '../../domain/idea/paper_position.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Thin-журнал читает только VPS. Сделки живут в paper-ledger, а показанные
/// идеи, которые завершились до создания сделки, читаются из server lifecycle.
/// Ошибка чтения любого источника никогда не превращается в «история пуста».
class ServerJournalScreen extends StatefulWidget {
  const ServerJournalScreen({super.key, required this.pill});

  final int pill;

  @override
  State<ServerJournalScreen> createState() => _ServerJournalScreenState();
}

class _ServerJournalScreenState extends State<ServerJournalScreen> {
  final JournalPaperClient _paper = JournalPaperClient();
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
      final trades = await _paper.trades();
      final rawSkips = await _api.getList('/api/v1/journal/skips?limit=100');
      final rawMisses = await _api.getList('/api/v1/journal/misses?limit=100');
      final noTrades = <_Skip>[
        for (final item in rawSkips)
          if (item is Map<String, dynamic>) _Skip.fromJson(item),
        for (final item in rawMisses)
          if (item is Map<String, dynamic>) _Skip.fromJson(item),
      ]..sort((a, b) => b.at.compareTo(a.at));
      if (!mounted) return;
      setState(() {
        _trades = trades;
        _skips = noTrades;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return _Failure(
        error: _error!,
        hasSnapshot: _trades != null,
        onRetry: _reload,
      );
    }
    if (_trades == null) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(28),
          child: BusyLine(label: 'Читаем серверный журнал…'),
        ),
      );
    }

    final section =
        JournalPill.values[widget.pill.clamp(0, JournalPill.values.length - 1)];
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
        _Status(
          text: '${trades.length} сделок в ledger · сейчас ведётся ${live.length}',
          loading: loading,
          onReload: onReload,
        ),
        const SizedBox(height: 12),
        SectionLabel('Сейчас ведёт сервер · ${live.length}'),
        const SizedBox(height: 8),
        if (live.isEmpty)
          const _Empty(
            title: 'Активных paper-сделок нет',
            note: 'Подтверждённая идея появится здесь сразу после создания '
                'серверной сделки, даже если заявка ещё ждёт входа.',
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
          const _Empty(
            title: 'Закрытых сделок пока нет',
            note: 'CLOSED и CANCELLED остаются на VPS после завершения.',
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
    final statusColor = switch (trade.status) {
      PaperPositionStatus.pending => C.accent,
      PaperPositionStatus.open => C.green,
      PaperPositionStatus.closed => (trade.resultR ?? 0) >= 0 ? C.green : C.red,
      PaperPositionStatus.cancelled => C.muted,
    };
    return Pressable(
      onTap: trade.ideaId.isEmpty ? null : () => controller.openSignal(trade.ideaId),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text(trade.symbol, style: T.jost(16))),
                Text(
                  trade.long ? 'LONG' : 'SHORT',
                  style: T.mono(
                    10,
                    weight: 700,
                    color: trade.long ? C.green : C.red,
                  ),
                ),
                const SizedBox(width: 8),
                OutlineBadge(
                  label: _statusLabel(trade.status),
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
                PaperPositionStatus.open => trade.runnerActive
                    ? 'TP3 пройден. Остаток позиции ведёт trailing runner.'
                    : 'Позиция открыта. Взято целей ${trade.tpsTaken} из ${trade.tpsTotal}.',
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
                if (trade.runnerActive) const TagChip('RUNNER · TRAILING'),
                if (trade.resultR != null)
                  TagChip(
                    '${trade.resultRealized ? 'зафикс.' : 'результат'} ${_r(trade.resultR!)}',
                  ),
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
            if (!trade.status.live && trade.closedAt != null) ...[
              const SizedBox(height: 4),
              Text(
                'закрыта ${_moment(trade.closedAt!)}',
                style: T.mono(9.5, color: C.faint),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _Skips extends StatelessWidget {
  const _Skips({
    required this.rows,
    required this.loading,
    required this.onReload,
  });

  final List<_Skip> rows;
  final bool loading;
  final Future<void> Function() onReload;

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
        children: [
          _Status(
            text: '${rows.length} идей завершились без paper-сделки',
            loading: loading,
            onReload: onReload,
          ),
          const SizedBox(height: 12),
          if (rows.isEmpty)
            const _Empty(
              title: 'Завершённых без сделки идей пока нет',
              note: 'Явный отказ, отмена, пропуск или истечение показанной идеи '
                  'останутся здесь с серверной причиной.',
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
                          Text(
                            row.score.toStringAsFixed(0),
                            style: T.mono(11, color: C.accent),
                          ),
                        ],
                      ),
                      const SizedBox(height: 6),
                      Text(row.reason, style: T.body(11.5, weight: 700)),
                      if (row.comment.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text(
                          row.comment,
                          style: T.body(11, color: C.muted, height: 1.4),
                        ),
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
  const _Metrics({
    required this.trades,
    required this.loading,
    required this.onReload,
  });

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
    final money = journalMoneyMetrics(trades);
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _Status(
          text: '${trades.length} server paper-сделок',
          loading: loading,
          onReload: onReload,
        ),
        const SizedBox(height: 12),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Фактическая выборка'),
              const SizedBox(height: 8),
              _Metric('Закрыто', '${closed.length}'),
              if (money.isEmpty) ...[
                const _Metric('Сумма', '—'),
                const _Metric('Среднее', '—'),
              ] else
                for (final metric in money) ...[
                  _Metric(
                    'Сумма, ${_currencyLabel(metric.currency)}',
                    _money(metric.sum, metric.currency),
                  ),
                  _Metric(
                    'Среднее, ${_currencyLabel(metric.currency)}',
                    _money(metric.average, metric.currency),
                  ),
                ],
              _Metric(
                'Винрейт',
                results.isEmpty ? '—' : '${(wins / results.length * 100).round()}%',
              ),
              const SizedBox(height: 8),
              Text(
                closed.length < 30
                    ? 'Выборка пока мала: это контроль исполнения, а не доказательство качества стратегии.'
                    : 'Деньги считаются только по закрытым server paper-сделкам и не смешиваются между валютами.',
                style: T.body(10.5, color: C.muted, height: 1.45),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Status extends StatelessWidget {
  const _Status({
    required this.text,
    required this.loading,
    required this.onReload,
  });

  final String text;
  final bool loading;
  final Future<void> Function() onReload;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Expanded(child: SectionLabel('Источник истины · VPS')),
                OutlineBadge(
                  label: 'SERVER',
                  color: C.green,
                  borderColor: C.greenBorder,
                  fontWeight: 700,
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(text, style: T.body(11.5, color: C.textSecondary)),
            const SizedBox(height: 9),
            if (loading)
              const BusyLine(label: 'Обновляем журнал…')
            else
              ActionButton(label: 'Обновить', dense: true, onTap: onReload),
          ],
        ),
      );
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

class _Empty extends StatelessWidget {
  const _Empty({required this.title, required this.note});

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
  const _Failure({
    required this.error,
    required this.hasSnapshot,
    required this.onRetry,
  });

  final String error;
  final bool hasSnapshot;
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
                Text(
                  hasSnapshot ? 'Журнал не обновился' : 'Журнал недоступен',
                  style: T.jost(17),
                ),
                const SizedBox(height: 6),
                Text(
                  error,
                  style: T.body(11, color: C.warning, height: 1.45),
                ),
                const SizedBox(height: 8),
                Text(
                  'Пустой экран не показываем: пока server ledger не прочитан, '
                  'система не утверждает, что сделок нет.',
                  style: T.body(10.5, color: C.muted, height: 1.45),
                ),
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
        at: DateTime.tryParse('${json['skipped_at'] ?? ''}')?.toLocal() ??
            DateTime.now(),
      );
}

String _statusLabel(PaperPositionStatus status) => switch (status) {
      PaperPositionStatus.pending => 'ЖДЁТ ВХОД',
      PaperPositionStatus.open => 'ОТКРЫТА',
      PaperPositionStatus.closed => 'ЗАКРЫТА',
      PaperPositionStatus.cancelled => 'ОТМЕНЕНА',
    };

String _price(double value) => value
    .toStringAsFixed(value.abs() < 0.01 ? 6 : 4)
    .replaceAll(RegExp(r'0+$'), '')
    .replaceAll(RegExp(r'\.$'), '')
    .replaceAll('.', ',');

String _r(double value) =>
    '${value >= 0 ? '+' : '−'}${value.abs().toStringAsFixed(2).replaceAll('.', ',')}R';

String _currencyLabel(String currency) => switch (currency.toUpperCase()) {
      'RUB' => '₽',
      'USDT' => 'USDT',
      final other => other,
    };

String _money(double value, String currency) {
  final decimals = currency.toUpperCase() == 'RUB' ? 2 : 4;
  final absolute = value
      .abs()
      .toStringAsFixed(decimals)
      .replaceAll(RegExp(r'0+$'), '')
      .replaceAll(RegExp(r'\.$'), '')
      .replaceAll('.', ',');
  final sign = value > 0 ? '+' : (value < 0 ? '−' : '');
  return '$sign$absolute ${_currencyLabel(currency)}';
}

String _moment(DateTime value) {
  final v = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(v.day)}.${two(v.month)} ${two(v.hour)}:${two(v.minute)}';
}