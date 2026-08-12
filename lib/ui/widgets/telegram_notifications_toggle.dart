import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

class TelegramNotificationsToggle extends StatefulWidget {
  const TelegramNotificationsToggle({super.key});

  @override
  State<TelegramNotificationsToggle> createState() =>
      _TelegramNotificationsToggleState();
}

class _TelegramNotificationsToggleState
    extends State<TelegramNotificationsToggle> {
  final ApiClient _api = ApiClient();
  bool? _enabled;
  bool _configured = false;
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await _api.get('/api/v1/notifications/telegram');
      if (!mounted) return;
      setState(() {
        _enabled = data['enabled'] == true;
        _configured = data['configured'] == true;
        _error = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _set(bool enabled) async {
    if (_saving || !_configured) return;
    final previous = _enabled;
    setState(() {
      _enabled = enabled;
      _saving = true;
      _error = null;
    });
    try {
      final data = await _api.put(
        '/api/v1/notifications/telegram',
        body: {'enabled': enabled},
      );
      if (!mounted) return;
      setState(() {
        _enabled = data['enabled'] == true;
        _configured = data['configured'] == true;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _enabled = previous;
        _error = e.message;
      });
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  void dispose() {
    _api.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.only(top: 9, bottom: 3),
              child: SectionLabel('Telegram'),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 10),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Уведомления в Telegram',
                          style: T.body(13, weight: 700),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          _configured
                              ? 'Новые идеи и paper-события. Идея приходит с H1-графиком, уровнями и ссылкой в приложение.'
                              : 'Telegram не настроен на сервере.',
                          style: T.body(11, color: C.muted, height: 1.4),
                        ),
                        if (_error != null) ...[
                          const SizedBox(height: 4),
                          Text(
                            _error!,
                            style: T.body(10.5, color: C.red, height: 1.4),
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(width: 11),
                  if (_enabled == null || _saving)
                    const SizedBox(
                      width: 34,
                      child: Center(child: BusyDot()),
                    )
                  else
                    AppToggle(
                      value: _enabled!,
                      onChanged: _configured ? _set : (_) {},
                    ),
                ],
              ),
            ),
          ],
        ),
      );
}
