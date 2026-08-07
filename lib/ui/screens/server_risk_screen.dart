import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Риск thin-клиента читает и меняет именно серверный risk-state.
///
/// Лимиты пока versioned и не редактируются с телефона: тихо менять числа,
/// по которым уже выпущены идеи, нельзя. Зато здесь есть фактический расход
/// лимитов и настоящий kill switch, а не справочный текст.
class ServerRiskScreen extends StatefulWidget {
  const ServerRiskScreen({super.key});

  @override
  State<ServerRiskScreen> createState() => _ServerRiskScreenState();
}

class _ServerRiskScreenState extends State<ServerRiskScreen> {
  final ApiClient _api = ApiClient();
  Map<String, dynamic>? _data;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.get('/api/v1/risk/dashboard');
      if (mounted) setState(() {
        _data = data;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _toggle() async {
    if (_busy || _data == null) return;
    setState(() => _busy = true);
    try {
      final halted = _data!['kill_switch'] == true;
      final data = await _api.post(
        halted ? '/api/v1/risk/resume' : '/api/v1/risk/halt',
        body: {'reason': halted ? 'возобновлено владельцем из приложения' : 'остановлено владельцем из приложения'},
      );
      if (mounted) setState(() {
        _data = data;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    if (data == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: _error == null
              ? const BusyLine(label: 'Читаем серверный риск-профиль…')
              : Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(_error!, style: T.body(11, color: C.warning)),
                    const SizedBox(height: 10),
                    ActionButton(label: 'Повторить', onTap: _load),
                  ],
                ),
        ),
      );
    }

    final kill = data['kill_switch'] == true;
    final limits = data['limits'] is List ? data['limits'] as List : const [];
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  const Expanded(child: SectionLabel('Исполнение и защита')),
                  OutlineBadge(
                    label: kill ? 'STOP' : '${data['execution_mode'] ?? 'PAPER'}',
                    color: kill ? C.red : C.green,
                    borderColor: kill ? C.redBorder : C.greenBorder,
                    fontWeight: 700,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                data['paper_only'] == true
                    ? 'Боевые заявки программно закрыты: сейчас ведётся paper-контур.'
                    : 'Проверьте execution gate перед любым live-действием.',
                style: T.body(11, color: C.muted, height: 1.45),
              ),
              const SizedBox(height: 10),
              if (_busy)
                const BusyLine(label: 'Меняем server risk-state…')
              else
                ActionButton(
                  label: kill ? 'Возобновить новые входы' : 'Аварийно запретить новые входы',
                  primary: !kill,
                  color: kill ? C.accent : C.red,
                  onTap: _toggle,
                ),
            ],
          ),
        ),
        const SizedBox(height: 10),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Лимиты · server source of truth'),
              const SizedBox(height: 8),
              for (final raw in limits)
                if (raw is Map)
                  _Limit(
                    label: '${raw['label'] ?? raw['name'] ?? ''}',
                    limit: _pct(raw['limit']),
                    used: _pct(raw['used']),
                    breached: raw['breached'] == true,
                  ),
              const SizedBox(height: 8),
              Text(
                'Лимиты versioned вместе с торговой логикой. Менять их '
                'произвольно с телефона нельзя: иначе старая идея и её '
                'config_hash перестанут воспроизводиться. Следующий шаг — '
                'безопасные профили 90/10 · 80/20 · 70/30 с новой версией конфигурации.',
                style: T.body(10.5, color: C.faint, height: 1.45),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _Limit extends StatelessWidget {
  const _Limit({required this.label, required this.limit, required this.used, required this.breached});
  final String label;
  final String limit;
  final String used;
  final bool breached;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 6),
        child: Row(
          children: [
            Expanded(child: Text(label, style: T.body(11.5, color: C.muted))),
            Text('$used / $limit',
                style: T.mono(11.5, color: breached ? C.red : C.text)),
          ],
        ),
      );
}

String _pct(Object? raw) {
  final value = double.tryParse('$raw');
  if (value == null) return '—';
  return '${(value * 100).toStringAsFixed(value * 100 < 1 ? 2 : 1).replaceAll('.', ',')}%';
}
