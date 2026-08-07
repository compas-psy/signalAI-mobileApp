import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'vector_icon.dart';

/// Явный первый запуск thin-клиента.
///
/// Без device token сервер обязан отвечать 401. Раньше пользователь видел
/// обычный экран с «движок недоступен» и должен был сам догадаться, что
/// привязка спрятана в «Контроль → Интеграции». Для личного приложения это
/// не ошибка сети, а обязательный шаг установки, поэтому показываем его как
/// отдельное состояние.
class EngineSetupGate extends StatelessWidget {
  const EngineSetupGate({
    super.key,
    required this.issue,
    required this.baseUrl,
    required this.onConfigure,
  });

  final String issue;
  final String baseUrl;
  final VoidCallback onConfigure;

  @override
  Widget build(BuildContext context) => ColoredBox(
        color: C.bg,
        child: SafeArea(
          child: Center(
            child: SingleChildScrollView(
              padding: const EdgeInsets.all(24),
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 460),
                child: SectionCard(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Center(child: BrandMark(size: 62)),
                      const SizedBox(height: 16),
                      Text('Подключить SignalAI', style: T.jost(22)),
                      const SizedBox(height: 8),
                      Text(
                        'Приложение установлено, но это устройство ещё не '
                        'привязано к вашему серверу. Без привязки сервер '
                        'специально не отдаёт идеи, позиции и пакеты.',
                        style: T.body(12.5, color: C.muted, height: 1.5),
                      ),
                      const SizedBox(height: 14),
                      InsetBox(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const SectionLabel('Сервер', color: C.faint),
                            const SizedBox(height: 5),
                            Text(
                              baseUrl.isEmpty ? 'адрес не задан' : baseUrl,
                              style: T.mono(12, weight: 600),
                            ),
                            const SizedBox(height: 8),
                            Text(
                              issue,
                              style: T.body(11, color: C.warning, height: 1.45),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(height: 14),
                      Text(
                        'Токен устройства сохраняется только в Android '
                        'Keystore. Биржевые торговые ключи в APK не попадают.',
                        style: T.body(10.5, color: C.faint, height: 1.45),
                      ),
                      const SizedBox(height: 16),
                      ActionButton(
                        label: 'Привязать устройство',
                        primary: true,
                        onTap: onConfigure,
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      );
}
