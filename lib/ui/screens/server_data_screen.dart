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
  String? _action;
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final status = await _api.get('/api/v1/market/status');
      if (mounted) setState(() {
        _status = status;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
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
                _Row('Последний бар', '${status['latest_bar'] ?? '—'}'),
                if ('${status['note'] ?? ''}'.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text('${status['note']}',
                        style: T.body(10.5, color: C.warning, height: 1.4)),
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
            Flexible(child: Text(value, textAlign: TextAlign.right, style: T.mono(11.5))),
          ],
        ),
      );
}
