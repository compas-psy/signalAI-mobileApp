import 'package:flutter/material.dart';

import '../../data/api/capacity_status_client.dart';
import '../../domain/models/capacity_status.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Lazy, read-only capacity status placed above the existing Data screen.
class CapacityStatusPanel extends StatefulWidget {
  const CapacityStatusPanel({
    super.key,
    required this.child,
    this.loader,
  });

  final Widget child;
  final Future<CapacityStatus> Function()? loader;

  @override
  State<CapacityStatusPanel> createState() => _CapacityStatusPanelState();
}

class _CapacityStatusPanelState extends State<CapacityStatusPanel> {
  CapacityStatus? _status;
  Object? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final loader = widget.loader ?? CapacityStatusClient().latest;
      final value = await loader();
      if (!mounted) return;
      setState(() {
        _status = value;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 4),
            child: _loading
                ? const _CapacityLoadingCard()
                : _error != null
                    ? const _CapacityUnavailableCard()
                    : CapacityStatusCard(status: _status!),
          ),
          Expanded(child: widget.child),
        ],
      );
}

class _CapacityLoadingCard extends StatelessWidget {
  const _CapacityLoadingCard();

  @override
  Widget build(BuildContext context) => const SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionLabel('Ресурсы сервера'),
            SizedBox(height: 10),
            BusyBar(),
            SizedBox(height: 8),
            Text('Считываю текущую ёмкость сервера…'),
          ],
        ),
      );
}

class _CapacityUnavailableCard extends StatelessWidget {
  const _CapacityUnavailableCard();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Ресурсы сервера'),
            const SizedBox(height: 7),
            Text(
              'Статус ресурсов сейчас недоступен. Этот экран ничего не '
              'управляет и не влияет на торговый движок.',
              style: T.body(11.5, color: C.warning, height: 1.45),
            ),
          ],
        ),
      );
}

class CapacityStatusCard extends StatelessWidget {
  const CapacityStatusCard({
    super.key,
    required this.status,
  });

  final CapacityStatus status;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Ресурсы сервера')),
                Text(
                  _clock(status.collectedAt),
                  style: T.mono(10, color: C.faint),
                ),
              ],
            ),
            const SizedBox(height: 9),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                _metric('Память', _percent(status.memoryUsedRatio)),
                _metric('Диск', _percent(status.diskUsedRatio)),
                _metric('Inodes', _percent(status.inodeUsedRatio)),
                _metric('Очередь', '${status.executionQueueDepth}'),
                _metric(
                  'Queue lag',
                  '${status.executionQueueLagSeconds.toStringAsFixed(0)} c',
                ),
                _metric(
                  'Ollama',
                  status.ollamaReachable ? 'доступна' : 'недоступна',
                ),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              'Postgres: ${status.postgresConnections} соединений · '
              'scheduler lag ${status.schedulerLagSeconds.toStringAsFixed(0)} c · '
              'swap ${_bytes(status.swapUsedBytes)}',
              style: T.body(10.5, color: C.muted, height: 1.4),
            ),
            if (status.probeErrors.isNotEmpty) ...[
              const SizedBox(height: 10),
              InsetBox(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Часть метрик недоступна',
                      style: T.body(11, weight: 700, color: C.warning),
                    ),
                    const SizedBox(height: 4),
                    for (final error in status.probeErrors)
                      Text(
                        error,
                        style: T.mono(9.5, color: C.muted),
                      ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 12),
            _AutopilotHistory(remediation: status.latestRemediation),
            const SizedBox(height: 8),
            Text(
              'Только наблюдение: открытие этого экрана не запускает cleanup, '
              'не выгружает Ollama и не меняет режим торговли.',
              style: T.body(10.5, color: C.faint, height: 1.4),
            ),
          ],
        ),
      );

  Widget _metric(String label, String value) => Container(
        width: 118,
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 9),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: T.body(9.5, color: C.faint)),
            const SizedBox(height: 3),
            Text(value, style: T.mono(12, color: C.text)),
          ],
        ),
      );

  String _percent(double? value) =>
      value == null ? '—' : '${(value * 100).round()}%';

  String _clock(DateTime value) {
    final local = value.toLocal();
    final hour = local.hour.toString().padLeft(2, '0');
    final minute = local.minute.toString().padLeft(2, '0');
    return '$hour:$minute';
  }

  String _bytes(int value) {
    if (value <= 0) return '0 B';
    const mb = 1024 * 1024;
    const gb = 1024 * mb;
    if (value >= gb) return '${(value / gb).toStringAsFixed(1)} GB';
    if (value >= mb) return '${(value / mb).toStringAsFixed(0)} MB';
    return '$value B';
  }
}

class _AutopilotHistory extends StatelessWidget {
  const _AutopilotHistory({required this.remediation});

  final CapacityRemediation? remediation;

  @override
  Widget build(BuildContext context) {
    final value = remediation;
    if (value == null) {
      return InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Последнее событие автопилота',
              style: T.body(10, weight: 700, color: C.textSecondary),
            ),
            const SizedBox(height: 4),
            Text(
              'Автопилот ещё не фиксировал давление на ресурсы.',
              style: T.body(11, color: C.muted, height: 1.4),
            ),
          ],
        ),
      );
    }

    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'Последнее событие автопилота',
                  style: T.body(10, weight: 700, color: C.textSecondary),
                ),
              ),
              OutlineBadge(
                label: value.pressureState,
                color: _stateColor(value.pressureState),
                borderColor: _stateColor(value.pressureState),
                background: C.inset,
                fontWeight: 700,
              ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            '${_date(value.occurredAt)} · effective ${value.effectiveState}',
            style: T.mono(9.5, color: C.faint),
          ),
          const SizedBox(height: 5),
          Text(
            'Ollama: ${value.ollamaStatus}',
            style: T.body(10.5, color: C.muted),
          ),
          Text(
            'Retention: ${value.retentionStatus}',
            style: T.body(10.5, color: C.muted),
          ),
          if (value.retentionDeletedFiles > 0)
            Text(
              'Удалено: ${value.retentionDeletedFiles} файлов · '
              '${value.retentionDeletedBytes} байт',
              style: T.body(10, color: C.faint),
            ),
          if (value.reasons.isNotEmpty) ...[
            const SizedBox(height: 4),
            Text(
              value.reasons.join(' · '),
              style: T.mono(9, color: C.faint),
            ),
          ],
        ],
      ),
    );
  }

  Color _stateColor(String state) => switch (state) {
        'CRITICAL' => C.red,
        'PRESSURE' => C.warning,
        'RECOVERING' => C.accent,
        _ => C.green,
      };

  String _date(DateTime value) {
    final local = value.toLocal();
    final day = local.day.toString().padLeft(2, '0');
    final month = local.month.toString().padLeft(2, '0');
    final hour = local.hour.toString().padLeft(2, '0');
    final minute = local.minute.toString().padLeft(2, '0');
    return '$day.$month $hour:$minute';
  }
}
