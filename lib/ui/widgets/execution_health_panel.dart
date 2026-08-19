import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Read-only owner view of SAI-029 execution evidence.
///
/// Violations stay attached to their exact execution intent instead of being
/// collapsed into an aggregate. The aggregate is only a headline over the same
/// rows, so an owner can always answer "which trade was unhealthy and why?".
class ExecutionHealthPanel extends StatelessWidget {
  const ExecutionHealthPanel({
    super.key,
    required this.data,
  });

  final Map<String, dynamic> data;

  @override
  Widget build(BuildContext context) {
    final rawItems = data['items'];
    final items = rawItems is List ? rawItems.whereType<Map>().toList() : const <Map>[];
    final aggregate = data['aggregate'] is Map
        ? data['aggregate'] as Map
        : const <String, dynamic>{};
    final total = _int(aggregate['total_intents']);
    final violations = _int(aggregate['violation_intents']);

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Здоровье исполнения')),
              OutlineBadge(
                label: violations == 0 ? 'OK' : '$violations / $total',
                color: violations == 0 ? C.green : C.warning,
                borderColor: violations == 0 ? C.greenBorder : C.warning,
                fontWeight: 700,
              ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            total == 0
                ? 'Execution-intent ещё не создавались.'
                : '$violations из $total с нарушениями · показаны последние execution-intent',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
          if (items.isNotEmpty) ...[
            const SizedBox(height: 10),
            for (var index = 0; index < items.length; index++) ...[
              _ExecutionHealthRow(item: items[index]),
              if (index != items.length - 1) const SizedBox(height: 8),
            ],
          ],
        ],
      ),
    );
  }
}

class _ExecutionHealthRow extends StatelessWidget {
  const _ExecutionHealthRow({required this.item});

  final Map item;

  @override
  Widget build(BuildContext context) {
    final rawViolations = item['violations'];
    final violations = rawViolations is List
        ? rawViolations.whereType<Map>().toList()
        : const <Map>[];
    final ws = '${item['websocket_state'] ?? 'NOT_CONFIGURED'}';
    final hasViolation = violations.isNotEmpty;

    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  '${item['instrument_id'] ?? '—'}',
                  style: T.mono(11.5, color: C.text),
                ),
              ),
              OutlineBadge(
                label: '${item['state'] ?? 'UNKNOWN'}',
                color: hasViolation ? C.warning : C.textSecondary,
                borderColor: hasViolation ? C.warning : C.border,
                fontWeight: 700,
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'decision→intent ${_latency(item['decision_to_intent_ms'])} · '
            'submit→ack ${_latency(item['submit_to_ack_ms'])}',
            style: T.mono(9.5, color: C.muted),
          ),
          const SizedBox(height: 3),
          Text(
            'fill ${_bps(item['fill_deviation_bps'])} · '
            'protection ${_seconds(item['protection_arm_ms'])} / '
            'SLA ${_seconds(item['protection_sla_ms'])}',
            style: T.mono(9.5, color: C.muted),
          ),
          const SizedBox(height: 3),
          Text(
            'reconcile ${_int(item['reconciliation_mismatch_count'])} · '
            'reject ${_int(item['rejected_order_count'])} · '
            'dedupe ${_int(item['duplicate_prevention_count'])} · '
            'WS $ws',
            style: T.mono(
              9.5,
              color: ws == 'HEALTHY'
                  ? C.muted
                  : ws == 'NOT_CONFIGURED'
                      ? C.faint
                      : C.warning,
            ),
          ),
          if (violations.isNotEmpty) ...[
            const SizedBox(height: 7),
            for (final violation in violations)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('• ', style: T.body(10.5, color: C.warning)),
                    Expanded(
                      child: Text(
                        '${violation['label'] ?? violation['code'] ?? 'Нарушение execution'}',
                        style: T.body(10.5, color: C.warning, height: 1.35),
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ],
      ),
    );
  }
}

int _int(Object? raw) => int.tryParse('$raw') ?? 0;

String _latency(Object? raw) {
  if (raw == null) return '—';
  final value = int.tryParse('$raw');
  if (value == null) return '—';
  return value < 1000 ? '$value мс' : _seconds(value);
}

String _seconds(Object? raw) {
  if (raw == null) return '—';
  final value = double.tryParse('$raw');
  if (value == null) return '—';
  return '${(value / 1000).toStringAsFixed(1).replaceAll('.', ',')} c';
}

String _bps(Object? raw) {
  if (raw == null) return '—';
  final value = double.tryParse('$raw');
  if (value == null) return '—';
  final sign = value > 0 ? '+' : '';
  return '$sign${value.toStringAsFixed(2).replaceAll('.', ',')} bp';
}
