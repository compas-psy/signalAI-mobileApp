import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Управление серверными данными: состояние + явные одноразовые перепроверки.
class ServerDataScreen extends StatefulWidget {
  const ServerDataScreen({super.key});

  @override
  State<ServerDataScreen> createState() => _ServerDataScreenState();
}

class _ServerDataScreenState extends State<ServerDataScreen> {
  final ApiClient _api = ApiClient();
  Map<String, dynamic>? _status;
  Map<String, dynamic>? _fortsRadar;
  String? _action;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final errors = <String>[];

    Future<Map<String, dynamic>?> read(String path, String label) async {
      try {
        return await _api.get(path);
      } catch (error) {
        errors.add('$label: $error');
        return null;
      }
    }

    final results = await Future.wait([
      read('/api/v1/market/status', 'Рынок'),
      read('/api/v1/diagnostics/forts-radar', 'FORTS Radar'),
    ]);
    if (!mounted) return;
    setState(() {
      _status = results[0];
      _fortsRadar = results[1];
      _error = errors.isEmpty ? null : errors.join('\n');
    });
  }

  Future<void> _run(String path) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _action = null;
    });
    try {
      final result = await _api.post(path, body: const {});
      if (!mounted) return;
      setState(() => _action = '${result['detail'] ?? 'готово'}');
      await _load();
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final status = _status;
    final roots = (_fortsRadar?['roots'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList(growable: false);
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Рынок · VPS'),
              const SizedBox(height: 7),
              if (status == null)
                const BusyLine(label: 'Читаем состояние данных…')
              else ...[
                _Row('Торгуемых инструментов', '${status['tradable'] ?? '—'}'),
                _Row('Инструментов с данными', '${status['with_data'] ?? '—'}'),
                _Row('Последний бар', '${status['last_bar_time'] ?? '—'}'),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('FORTS Radar · VPS'),
              const SizedBox(height: 6),
              Text(
                'Шесть базовых рынков: что сервер видит, что прошло допуск и '
                'есть ли сейчас сетап или сопровождаемая PAPER-сделка.',
                style: T.body(10.5, color: C.muted, height: 1.45),
              ),
              const SizedBox(height: 8),
              if (_fortsRadar == null)
                const BusyLine(label: 'Читаем FORTS pipeline…')
              else if (roots.isEmpty)
                Text(
                  'Сервер ответил без core-контрактов.',
                  style: T.body(10.5, color: C.warning),
                )
              else
                for (var i = 0; i < roots.length; i++) ...[
                  if (i > 0) const SizedBox(height: 6),
                  _RadarRootTile(data: roots[i]),
                ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Перепроверить сейчас'),
              const SizedBox(height: 6),
              Text(
                'Это одноразовые действия, не второе расписание. Нужны после '
                'изменения токенов или когда карточка выглядит устаревшей.',
                style: T.body(10.5, color: C.muted, height: 1.45),
              ),
              const SizedBox(height: 10),
              if (_busy)
                const BusyLine(label: 'Сервер выполняет проход…')
              else ...[
                ActionButton(
                  label: 'Перепроверить идеи и paper-сделки',
                  onTap: () => _run('/api/v1/control/ideas/reconcile'),
                ),
                const SizedBox(height: 8),
                ActionButton(
                  label: 'Обновить инвестиционные источники',
                  onTap: () => _run('/api/v1/control/research/refresh'),
                ),
              ],
              if (_action != null) ...[
                const SizedBox(height: 10),
                Text(_action!, style: T.body(10.5, color: C.green, height: 1.45)),
              ],
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(_error!, style: T.body(10.5, color: C.warning, height: 1.45)),
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          child: Text(
            'Телефон не ходит напрямую на MOEX/Bybit за рыночными данными. '
            'Это намеренно: VPN, гео телефона и закрытое приложение не должны '
            'останавливать сопровождение. Сбор и research работают на VPS.',
            style: T.body(10.5, color: C.faint, height: 1.45),
          ),
        ),
      ],
    );
  }
}

class _RadarRootTile extends StatefulWidget {
  const _RadarRootTile({required this.data});

  final Map<String, dynamic> data;

  @override
  State<_RadarRootTile> createState() => _RadarRootTileState();
}

class _RadarRootTileState extends State<_RadarRootTile> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final data = widget.data;
    final paper = data['paper'] is Map<String, dynamic>
        ? data['paper'] as Map<String, dynamic>
        : null;
    final idea = data['idea'] is Map<String, dynamic>
        ? data['idea'] as Map<String, dynamic>
        : null;
    final stage = '${data['stage'] ?? 'not_observed'}';
    final stageText = _stageLabel(stage);
    final active = stage.startsWith('paper_') || stage == 'setup';
    final stageColor = active
        ? C.green
        : stage == 'rejected' || stage == 'not_observed'
            ? C.warning
            : C.muted;

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () => setState(() => _expanded = !_expanded),
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${data['label'] ?? data['root'] ?? 'FORTS'} · ${data['symbol'] ?? '—'}',
                        style: T.body(12, color: C.text),
                      ),
                      const SizedBox(height: 2),
                      Text(stageText, style: T.body(10.5, color: stageColor)),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                Text(_expanded ? '▾' : '›', style: T.body(16, color: C.muted)),
              ],
            ),
            const SizedBox(height: 3),
            Text(
              '${data['primary_reason'] ?? '—'}',
              style: T.body(10.5, color: C.faint, height: 1.35),
            ),
            if (_expanded) ...[
              const SizedBox(height: 7),
              _RadarDetail('Оборот', _rub(data['turnover_rub'])),
              _RadarDetail('OI, ₽', _rub(data['oi_notional_rub'])),
              _RadarDetail('OI, контрактов', _plain(data['open_interest_contracts'])),
              _RadarDetail('Спред', _spread(data['spread_pct'])),
              _RadarDetail('Закрытых H1', _plain(data['closed_hourly_bars'])),
              _RadarDetail('До экспирации', _days(data['days_to_expiry'])),
              _RadarDetail('Снимок рынка', _time(data['snapshot_at'])),
              if (idea != null) ...[
                const SizedBox(height: 4),
                _RadarDetail(
                  'Сетап',
                  '${idea['status'] ?? '—'} · ${idea['strategy'] ?? '—'}',
                ),
                _RadarDetail('Сигнал', _time(idea['signal_time'])),
                _RadarDetail('Действует до', _time(idea['expires_at'])),
              ],
              if (paper != null) ...[
                const SizedBox(height: 4),
                _RadarDetail(
                  'PAPER',
                  '${paper['status'] ?? '—'} · ${_paperLabel('${paper['lifecycle'] ?? ''}')}',
                ),
                _RadarDetail('Текущий стоп', _plain(paper['current_stop'])),
                _RadarDetail('Взято целей', _plain(paper['tps_taken'])),
                _RadarDetail('Остаток позиции', _percent(paper['remaining_fraction'])),
                _RadarDetail('Последняя сверка', _time(paper['last_reconciled_at'])),
                if ('${paper['close_reason'] ?? ''}'.isNotEmpty)
                  _RadarDetail('Закрытие', '${paper['close_reason']}'),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

class _RadarDetail extends StatelessWidget {
  const _RadarDetail(this.name, this.value);

  final String name;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: Text(name, style: T.body(10, color: C.faint))),
            const SizedBox(width: 10),
            Flexible(
              child: Text(
                value,
                textAlign: TextAlign.right,
                style: T.mono(10.5, color: C.muted),
              ),
            ),
          ],
        ),
      );
}

String _stageLabel(String stage) => switch (stage) {
      'rejected' => 'не допущен',
      'ready_no_setup' => 'допущен · сетапа нет',
      'setup' => 'есть сетап',
      'paper_pending' => 'PAPER · лимит ждёт входа',
      'paper_open' => 'PAPER · позиция в работе',
      _ => 'не наблюдается',
    };

String _paperLabel(String lifecycle) => switch (lifecycle) {
      'limit' => 'LIMIT',
      'filled' => 'filled',
      'tp1' => 'TP1',
      'runner' => 'TP2 → runner',
      'closed' => 'closed',
      'cancelled' => 'cancelled',
      _ => lifecycle.isEmpty ? '—' : lifecycle,
    };

String _plain(Object? value) => value == null || '$value'.isEmpty ? '—' : '$value';

String _rub(Object? raw) {
  final value = double.tryParse('${raw ?? ''}');
  if (value == null) return '—';
  if (value.abs() >= 1000000000) return '${(value / 1000000000).toStringAsFixed(1)} млрд ₽';
  if (value.abs() >= 1000000) return '${(value / 1000000).toStringAsFixed(1)} млн ₽';
  if (value.abs() >= 1000) return '${(value / 1000).toStringAsFixed(0)} тыс ₽';
  return '${value.toStringAsFixed(0)} ₽';
}

String _spread(Object? raw) {
  final value = double.tryParse('${raw ?? ''}');
  return value == null ? '—' : '${(value * 100).toStringAsFixed(3)}%';
}

String _percent(Object? raw) {
  final value = raw is num ? raw.toDouble() : double.tryParse('${raw ?? ''}');
  return value == null ? '—' : '${(value * 100).toStringAsFixed(0)}%';
}

String _days(Object? raw) {
  final value = raw is num ? raw.toInt() : int.tryParse('${raw ?? ''}');
  return value == null ? '—' : '$value дн.';
}

String _time(Object? raw) {
  if (raw == null || '$raw'.isEmpty) return '—';
  final value = DateTime.tryParse('$raw');
  if (value == null) return '$raw';
  final local = value.toLocal();
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(local.day)}.${two(local.month)} ${two(local.hour)}:${two(local.minute)}';
}

class _Row extends StatelessWidget {
  const _Row(this.name, this.value);
  final String name;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 5),
        child: Row(
          children: [
            Expanded(child: Text(name, style: T.body(11.5, color: C.muted))),
            const SizedBox(width: 12),
            Flexible(
              child: Text(
                value,
                textAlign: TextAlign.right,
                style: T.mono(11.5),
              ),
            ),
          ],
        ),
      );
}
