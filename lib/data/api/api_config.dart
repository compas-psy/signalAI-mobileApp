/// Конфигурация подключения к мобильному гейтвею (Server B).
///
/// Задаётся на этапе сборки, чтобы в коде и в git не было ни адресов боевых
/// серверов, ни токенов:
///
/// ```
/// flutter run \
///   --dart-define=SIGNALAI_API_BASE_URL=https://api.signalai.ru \
///   --dart-define=SIGNALAI_DEVICE_TOKEN=…
/// ```
///
/// Если базовый адрес не задан, приложение работает на демо-данных макета
/// (см. [DemoRepository]) — это нормальный режим для просмотра интерфейса.
///
/// Ключи бирж и брокеров сюда не попадают никогда (ТЗ §11): они живут
/// зашифрованными на сервере, клиент оперирует только токеном устройства.
abstract final class ApiConfig {
  static const baseUrl = String.fromEnvironment('SIGNALAI_API_BASE_URL');

  /// Токен устройства. В боевом сценарии выдаётся при привязке устройства и
  /// хранится в Android Keystore; здесь — только для отладочных сборок.
  static const deviceToken = String.fromEnvironment('SIGNALAI_DEVICE_TOKEN');

  /// Есть ли настроенный бэкенд.
  static bool get isConfigured => baseUrl.isNotEmpty;

  /// Только HTTPS: гейтвей отдаёт торговые данные и принимает подтверждения.
  static bool get isSecure => baseUrl.startsWith('https://');

  static const requestTimeout = Duration(seconds: 20);
}
