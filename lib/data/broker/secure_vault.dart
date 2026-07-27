import '../../domain/broker/broker.dart';
import '../native_bridge.dart';

/// Торговые секреты: доступ к ним и подпись запросов.
///
/// Обёртка над нативным хранилищем поверх Android Keystore. Устроена так, что
/// секрет биржи в Dart не приходит вообще: наружу доступны только факт наличия
/// ключа, идентификатор ключа API и готовая подпись. Прочитать секрет отсюда
/// нельзя — метода нет, и это не упущение, а условие.
class SecureVault {
  const SecureVault({NativeBridge bridge = const NativeBridge()}) : _bridge = bridge;

  final NativeBridge _bridge;

  /// Имена секретов. Testnet и live хранятся раздельно: перепутать ключи
  /// тренировочного и живого счёта — самая дорогая из возможных опечаток.
  static String apiKeyName(String exchange, String mode) => '$exchange.$mode.key';
  static String apiSecretName(String exchange, String mode) => '$exchange.$mode.secret';

  Future<bool> get isAvailable => _bridge.vaultAvailable();

  /// Сохраняет пару «ключ + секрет».
  Future<void> saveKeys({
    required String exchange,
    required String mode,
    required String apiKey,
    required String apiSecret,
  }) async {
    await _bridge.vaultPut(apiKeyName(exchange, mode), apiKey.trim());
    await _bridge.vaultPut(apiSecretName(exchange, mode), apiSecret.trim());
  }

  Future<void> deleteKeys({required String exchange, required String mode}) async {
    await _bridge.vaultDelete(apiKeyName(exchange, mode));
    await _bridge.vaultDelete(apiSecretName(exchange, mode));
  }

  /// Заведены ли ключи для площадки и режима.
  Future<bool> hasKeys({required String exchange, required String mode}) async {
    final key = await _bridge.vaultHas(apiKeyName(exchange, mode));
    final secret = await _bridge.vaultHas(apiSecretName(exchange, mode));
    return key && secret;
  }

  /// Идентификатор ключа API — он уходит в заголовок запроса открытым текстом,
  /// поэтому читать его наружу можно.
  Future<String?> apiKey({required String exchange, required String mode}) =>
      _bridge.vaultGet(apiKeyName(exchange, mode));

  /// Подпись запроса. null — секрета нет либо канал недоступен.
  Future<String?> sign({
    required String exchange,
    required String mode,
    required String payload,
  }) =>
      _bridge.vaultSign(apiSecretName(exchange, mode), payload);

  /// Подтверждение сделки биометрией. false — не подтверждено.
  Future<bool> confirm({required String title, String subtitle = ''}) =>
      _bridge.biometricConfirm(title: title, subtitle: subtitle);

  Future<bool> get biometricsAvailable => _bridge.biometricsAvailable();

  /// Чем именно подтверждается сделка на этом устройстве.
  Future<ConfirmMethod> get confirmMethod async =>
      ConfirmMethod.parse(await _bridge.confirmMethod());

  /// Сырые факты детекции: SDK, защищённость экрана, коды canAuthenticate.
  Future<String?> get confirmMethodDetails => _bridge.confirmMethodDetails();
}
