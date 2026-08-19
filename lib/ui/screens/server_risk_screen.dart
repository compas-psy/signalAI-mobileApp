import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/execution_health_panel.dart';

/// Риск thin-клиента читает и меняет именно серверный risk-state.
class ServerRiskScreen extends StatefulWidget {
  const ServerRiskScreen({super.key, this.api});

  /// Инъекция нужна только для детерминированного widget-теста. В приложении
  /// экран по-прежнему создаёт обычный HTTPS ApiClient сам.
  final ApiClient? api;

  @override
  State<ServerRiskScreen> createState() => _ServerRiskScreenState();
}

class _ServerRiskScreenState extends State<ServerRiskScreen> {
  late final ApiClient _api;
  late final bool _ownsApi;
  Map<String, dynamic>? _data;
  Map<String, dynamic>? _executionHealth;
  String? _error;
  String? _executionHealthError;
  bool _busy = false;
  bool _confirmFlatten = false;

  @override
  void initState() {
    super.initState();
    _ownsApi = widget.api == null;
    _api = widget.api ?? ApiClient();
    _load();
  }

  @override
  void dispose() {
    if (_ownsApi) _api.close();
    super.dispose();
  }

  Future<void> _load() async {
    await Future.wait([
      _loadRisk(),
      _loadExecutionHealth(),
    ]);
  }

  Future<void> _loadRisk() async {
    try {
      final data = await _api.get('/api/v1/risk/dashboard');
      if (mounted) {
        setState(() {
          _data = data;
          _error = null;
          _confirmFlatten = false;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = '$error');
      }
    }
  }

  Future<void> _loadExecutionHealth() async {
    try {
      final data = await _api.get('/api/v1/execution/health?limit=20');
      if (mounted) {
        setState(() {
          _executionHealth = data;
          _executionHealthError = null;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() {
          _executionHealthError = '$error';
        });
      }
    }
  }

  Future<void> _setLevel(
    String level, {
    bool confirmFlattenAll = false,
  }) async {
    if (_busy || _data == null) return;
    setState(() {
      _busy = true;
      _confirmFlatten = false;
    });
    try {
      final data = await _api.post(
        '/api/v1/risk/kill-switch',
        body: {
          'level': level,
          'reason': _reasonFor(level),
          'confirm_flatten_all': confirmFlattenAll,
        },
      );
      if (mounted) {
        setState(() {
          _data = data;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) {
        setState(() => _error = '$error');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _resume() async {
    if (_busy || _data == null) return;
    setState(() {
      _busy = true;
      _confirmFlatten = false;
    });
    try {
      final data = await _api.post(
        '/api/v1/risk/resume',
        body: const {'reason': 'возобновлено владельцем из приложения'},
      );
      if (mounted) {
        setState(() {
          _data = data;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  String _reasonFor(String level) => switch (level) {
        'CANCEL_PENDING_ENTRIES' =>
          'ожидающие входы отменены владельцем из приложения',
        'FLATTEN_ALL' =>
          'FLATTEN_ALL явно подтверждён владельцем из приложения',
        _ => 'новые входы остановлены владельцем из приложения',
      };

  String _level(Map<String, dynamic> data) {
    final exact = '${data['kill_switch_level'] ?? ''}'.trim();
    if (exact == 'CLEAR' ||
        exact == 'HALT_NEW_ENTRIES' ||
        exact == 'CANCEL_PENDING_ENTRIES' ||
        exact == 'FLATTEN_ALL') {
      return exact;
    }
    // Совместимость с сервером до SAI-028: старый boolean true всегда
    // трактуем как наименее разрушительный активный уровень, а не как CLEAR.
    return data['kill_switch'] == true ? 'HALT_NEW_ENTRIES' : 'CLEAR';
  }

  String _badge(String level, Map<String, dynamic> data) => switch (level) {
        'HALT_NEW_ENTRIES' => 'HALT',
        'CANCEL_PENDING_ENTRIES' => 'CANCEL',
        'FLATTEN_ALL' => 'FLATTEN',
        _ => '${data['execution_mode'] ?? 'PAPER'}',
      };

  String _levelText(String level) => switch (level) {
        'HALT_NEW_ENTRIES' =>
          'Новые входы запрещены. Сверка, сопровождение и защита уже начатых сделок продолжаются.',
        'CANCEL_PENDING_ENTRIES' =>
          'Новые входы запрещены, а локальные ожидающие execution-intent отменяются до отправки на площадку.',
        'FLATTEN_ALL' =>
          'Зафиксирован аварийный уровень FLATTEN_ALL. Пока venue-adapter не подключён, приложение не выдаёт этот запрос за уже исполненное закрытие у брокера.',
        _ => dataPaperText,
      };

  static const dataPaperText =
      'Ни один аварийный уровень не активен. Выберите действие только если нужно ограничить execution.';

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

    final level = _level(data);
    final kill = level != 'CLEAR';
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
                    label: _badge(level, data),
                    color: kill ? C.red : C.green,
                    borderColor: kill ? C.redBorder : C.greenBorder,
                    fontWeight: 700,
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                _levelText(level),
                style: T.body(11, color: C.muted, height: 1.45),
              ),
              if (data['paper_only'] == true) ...[
                const SizedBox(height: 6),
                Text(
                  'Боевые заявки программно закрыты: сейчас ведётся paper-контур.',
                  style: T.body(10.5, color: C.faint, height: 1.4),
                ),
              ],
              if (_error != null) ...[
                const SizedBox(height: 8),
                Text(_error!, style: T.body(10.5, color: C.warning, height: 1.4)),
              ],
              const SizedBox(height: 12),
              if (_busy)
                const BusyLine(label: 'Меняем server risk-state…')
              else ...[
                ActionButton(
                  label: 'Запретить новые входы',
                  primary: !kill,
                  color: C.red,
                  onTap: level == 'HALT_NEW_ENTRIES'
                      ? null
                      : () => _setLevel('HALT_NEW_ENTRIES'),
                ),
                const SizedBox(height: 7),
                ActionButton(
                  label: 'Отменить ожидающие входы',
                  color: C.warning,
                  onTap: level == 'CANCEL_PENDING_ENTRIES'
                      ? null
                      : () => _setLevel('CANCEL_PENDING_ENTRIES'),
                ),
                const SizedBox(height: 7),
                ActionButton(
                  label: 'FLATTEN_ALL · аварийное закрытие',
                  color: C.red,
                  onTap: level == 'FLATTEN_ALL'
                      ? null
                      : () => setState(() => _confirmFlatten = true),
                ),
                if (_confirmFlatten) ...[
                  const SizedBox(height: 9),
                  InsetBox(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'FLATTEN_ALL — отдельное deliberate действие. '
                          'Оно сильнее обычного запрета новых входов.',
                          style: T.body(10.5, color: C.warning, height: 1.45),
                        ),
                        const SizedBox(height: 8),
                        ActionButton(
                          label: 'Подтвердить FLATTEN_ALL',
                          primary: true,
                          color: C.red,
                          dense: true,
                          onTap: () => _setLevel(
                            'FLATTEN_ALL',
                            confirmFlattenAll: true,
                          ),
                        ),
                        const SizedBox(height: 6),
                        ActionButton(
                          label: 'Отмена',
                          dense: true,
                          onTap: () => setState(() => _confirmFlatten = false),
                        ),
                      ],
                    ),
                  ),
                ],
                if (kill) ...[
                  const SizedBox(height: 9),
                  ActionButton(
                    label: 'Снять аварийный режим',
                    color: C.accent,
                    onTap: _resume,
                  ),
                ],
              ],
            ],
          ),
        ),
        const SizedBox(height: 10),
        if (_executionHealth != null)
          ExecutionHealthPanel(data: _executionHealth!)
        else
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Здоровье исполнения'),
                const SizedBox(height: 7),
                if (_executionHealthError == null)
                  const BusyLine(label: 'Читаем execution evidence…')
                else ...[
                  Text(
                    'Execution health сейчас недоступен. Kill-switch и лимиты '
                    'остаются рабочими независимо от этой диагностической панели.',
                    style: T.body(10.5, color: C.warning, height: 1.45),
                  ),
                  const SizedBox(height: 7),
                  ActionButton(
                    label: 'Повторить health',
                    dense: true,
                    onTap: _loadExecutionHealth,
                  ),
                ],
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
  const _Limit({
    required this.label,
    required this.limit,
    required this.used,
    required this.breached,
  });

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
            Text(
              '$used / $limit',
              style: T.mono(11.5, color: breached ? C.red : C.text),
            ),
          ],
        ),
      );
}

String _pct(Object? raw) {
  final value = double.tryParse('$raw');
  if (value == null) return '—';
  return '${(value * 100).toStringAsFixed(value * 100 < 1 ? 2 : 1).replaceAll('.', ',')}%';
}
