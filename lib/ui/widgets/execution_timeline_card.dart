import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../data/api/execution_timeline_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Lazy, read-only forensic history for one execution idea (SAI-051).
///
/// The card never infers provider execution from a requested command. It only
/// renders durable facts returned by the server timeline projection.
class ExecutionTimelineCard extends StatefulWidget {
  const ExecutionTimelineCard({
    super.key,
    required this.ideaId,
    this.client,
  });

  final String ideaId;
  final ExecutionTimelineClient? client;

  @override
  State<ExecutionTimelineCard> createState() => _ExecutionTimelineCardState();
}

class _ExecutionTimelineCardState extends State<ExecutionTimelineCard> {
  late final ExecutionTimelineClient _client =
      widget.client ?? ExecutionTimelineClient();

  ExecutionTimeline? _timeline;
  String? _error;
  bool _loading = false;

  Future<void> _load() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final timeline = await _client.fetch(ideaId: widget.ideaId);
      if (!mounted) return;
      setState(() => _timeline = timeline);
    } on ApiException catch (error) {
      if (!mounted) return;
      setState(() => _error = error.message);
    } catch (_) {
      if (!mounted) return;
      setState(() => _error = 'Не удалось загрузить историю исполнения.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('История исполнения', style: T.sectionLabel()),
          const SizedBox(height: 8),
          if (_loading)
            const BusyLine(label: 'Загружаю подтверждённые факты исполнения…')
          else if (_timeline == null) ...[
            if (_error != null) ...[
              Text(
                _error!,
                style: T.body(11.5, color: C.warning, height: 1.45),
              ),
              const SizedBox(height: 8),
            ],
            ActionButton(
              label: _error == null ? 'Показать историю' : 'Повторить',
              onTap: _load,
              dense: true,
            ),
          ] else if (_timeline!.events.isEmpty)
            Text(
              'Подтверждённых событий исполнения пока нет.',
              style: T.body(11.5, color: C.muted, height: 1.45),
            )
          else
            ..._timeline!.events.map(_TimelineRow.new),
        ],
      );
}

class _TimelineRow extends StatelessWidget {
  const _TimelineRow(this.event);

  final ExecutionTimelineEvent event;

  String get _title => switch (event.kind) {
        'INTENT_CREATED' => 'Исполнение создано',
        'MANAGEMENT_POLICY_FROZEN' => 'Правила сопровождения зафиксированы',
        'ORDER_CREATED' => 'Заявка создана',
        'ORDER_SUBMITTED' => 'Заявка отправлена',
        'ORDER_ACKNOWLEDGED' => 'Биржа подтвердила заявку',
        'FILL_RECORDED' => 'Исполнение по заявке',
        'PROTECTION_CREATED' => 'Защита создана',
        'PROTECTION_ARMED' => 'Защита выставлена',
        'PROTECTION_RECONCILED' => 'Защита сверена',
        'MANUAL_CLOSE_REQUESTED' => 'Команда закрыть зафиксирована',
        'MANUAL_REDUCE_REQUESTED' => 'Команда сократить зафиксирована',
        'MANUAL_TIGHTEN_STOP_REQUESTED' => 'Новый стоп зафиксирован',
        'MANUAL_RETURN_AUTO_REQUESTED' => 'Возврат в авто зафиксирован',
        _ when event.source == 'reconciliation' => 'Сверка с биржей',
        _ => event.kind,
      };

  String get _time {
    final value = event.occurredAt.toUtc();
    String two(int part) => part.toString().padLeft(2, '0');
    return '${two(value.day)}.${two(value.month)} '
        '${two(value.hour)}:${two(value.minute)}:${two(value.second)} UTC';
  }

  String? get _facts {
    final facts = event.facts;
    final parts = <String>[];

    void add(String label, String key) {
      final value = facts[key];
      if (value == null) return;
      final text = value.toString();
      if (text.isEmpty) return;
      parts.add('$label $text');
    }

    switch (event.source) {
      case 'fill':
        add('объём', 'quantity');
        add('цена', 'price');
        add('комиссия', 'fee_amount');
      case 'order':
        add('статус', 'status');
        add('объём', 'quantity');
        add('цена', 'limit_price');
        add('стоп', 'stop_price');
      case 'protection':
        add('статус', 'status');
        add('объём', 'quantity');
        add('стоп', 'stop_price');
      case 'manual_control':
        add('объём', 'quantity');
        add('стоп', 'stop_price');
      case 'reconciliation':
        add('результат', 'outcome');
    }
    return parts.isEmpty ? null : parts.join(' · ');
  }

  bool get _awaitsVenue =>
      event.source == 'manual_control' && event.facts['status'] == 'REQUESTED';

  @override
  Widget build(BuildContext context) {
    final facts = _facts;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 9),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: C.divider)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(_title, style: T.body(12, weight: 700)),
              ),
              const SizedBox(width: 10),
              Text(_time, style: T.mono(9.5, color: C.faint)),
            ],
          ),
          if (facts != null) ...[
            const SizedBox(height: 4),
            Text(facts, style: T.mono(10.5, color: C.muted)),
          ],
          if (_awaitsVenue) ...[
            const SizedBox(height: 5),
            Text(
              'Команда зафиксирована сервером. '
              'Биржа ещё не подтвердила исполнение.',
              style: T.body(10.5, color: C.warning, height: 1.4),
            ),
          ],
        ],
      ),
    );
  }
}
