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

/// Восстановить адрес из JSON, а токен — только из Android Keystore.
///
/// Старый plaintext `engine.device_token` переносится один раз. JSON сразу
/// переписывается без секрета; если Keystore недоступен, токен не остаётся на
/// диске и контур закрывается с понятной причиной.
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
  final hadPlaintext = saved?.containsKey('device_token') == true;
  final vaultAvailable = await bridge.vaultAvailable();

  if (!vaultAvailable) {
    if (hadPlaintext) await store.write('engine', {'base_url': savedUrl});
    return EngineRuntimeCredentials(
      baseUrl: effectiveUrl,
      deviceToken: '',
      issue: effectiveUrl.isEmpty
          ? 'Адрес движка не задан.'
          : 'Устройство не привязано: Android Keystore недоступен.',
    );
  }

  var token = '';
  if (legacy.isNotEmpty) {
    if (await bridge.putEngineDeviceToken(legacy)) token = legacy;
  } else {
    token = await bridge.engineDeviceToken() ?? '';
  }
  if (hadPlaintext) await store.write('engine', {'base_url': savedUrl});

  ApiConfig.setDeviceToken(token);
  return EngineRuntimeCredentials(
    baseUrl: effectiveUrl,
    deviceToken: token,
    issue: effectiveUrl.isEmpty
        ? 'Адрес движка не задан.'
        : (token.isEmpty
            ? 'Устройство не привязано: задайте токен в «Подключениях».'
            : null),
  );
}
