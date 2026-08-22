import '../local_store.dart';
import '../native_bridge.dart';
import 'api_config.dart';

/// Runtime-конфигурация одного Dart-изолята.
///
/// Статические override не пересекают границу изолятов, поэтому UI и Android
/// background обязаны независимо вызвать [restoreEngineRuntime] до создания
/// `EngineClient`.
class EngineRuntimeCredentials {
  const EngineRuntimeCredentials({
    required this.baseUrl,
    required this.deviceToken,
    this.issue,
  });

  final String baseUrl;
  final String deviceToken;
  final String? issue;

  bool get ready =>
      baseUrl.startsWith('https://') && deviceToken.isNotEmpty && issue == null;
}

/// Восстановить адрес из JSON, а active-device token — только из Keystore.
///
/// Предыдущая версия хранила общий bootstrap secret как `device_token`.
/// Его нельзя переносить: сервер выдаёт активный bearer только после pairing.
/// Поэтому миграция стирает legacy секрет и требует перепривязку, а не пытается
/// угадать его тип и случайно открыть business API старым credential.
Future<EngineRuntimeCredentials> restoreEngineRuntime(
  LocalStore store,
  NativeBridge bridge,
) async {
  final saved = await store.read('engine');
  final savedUrl = saved?['base_url'] as String? ?? '';
  ApiConfig.setBaseUrl(savedUrl);
  ApiConfig.setDeviceToken('');

  final effectiveUrl = ApiConfig.baseUrl;
  final legacy = saved?['device_token'] as String? ?? '';
  final enrolled = saved?['device_enrollment_v1'] == true;
  final deviceId = saved?['device_id'] as String?;
  final hadLegacy = legacy.isNotEmpty || !enrolled;
  final vaultAvailable = await bridge.vaultAvailable();

  if (!vaultAvailable) {
    if (hadLegacy) await store.write('engine', {'base_url': savedUrl});
    return EngineRuntimeCredentials(
      baseUrl: effectiveUrl,
      deviceToken: '',
      issue: effectiveUrl.isEmpty
          ? 'Адрес движка не задан.'
          : 'Устройство не привязано: Android Keystore недоступен.',
    );
  }

  if (hadLegacy) {
    // Explicitly remove the old raw value from both stores.  It might be the
    // bootstrap secret and must never become a runtime API bearer again.
    await bridge.deleteEngineDeviceToken();
    await store.write('engine', {
      'base_url': savedUrl,
      'device_id': ?deviceId,
    });
    return EngineRuntimeCredentials(
      baseUrl: effectiveUrl,
      deviceToken: '',
      issue: effectiveUrl.isEmpty
          ? 'Адрес движка не задан.'
          : 'Устройство не привязано: пройдите привязку заново.',
    );
  }

  final token = await bridge.engineDeviceToken() ?? '';

  ApiConfig.setDeviceToken(token);
  return EngineRuntimeCredentials(
    baseUrl: effectiveUrl,
    deviceToken: token,
    issue: effectiveUrl.isEmpty
        ? 'Адрес движка не задан.'
        : (token.isEmpty
            ? 'Устройство не привязано: пройдите привязку в «Подключениях».'
            : null),
  );
}
