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
  /// Адрес, зашитый при сборке. Остаётся значением по умолчанию.
  static const compiledBaseUrl = String.fromEnvironment('SIGNALAI_API_BASE_URL');

  /// Адрес, заданный владельцем в «Подключениях».
  ///
  /// До этого адрес движка существовал только как параметр сборки, и в
  /// приложении его не было видно вовсе: все ключи бирж приняты, а лента
  /// идей пуста — и объяснения этому на экране не находилось. Главная
  /// зависимость приложения обязана быть и видимой, и исправимой без
  /// пересборки.
  static String _override = '';

  static String get baseUrl =>
      _override.isNotEmpty ? _override : compiledBaseUrl;

  /// Пустая строка возвращает приложение к адресу из сборки.
  static void setBaseUrl(String value) => _override = value.trim();

  /// Задан ли адрес руками, а не сборкой.
  static bool get isOverridden => _override.isNotEmpty;

  /// Токен устройства из сборки.
  static const compiledDeviceToken =
      String.fromEnvironment('SIGNALAI_DEVICE_TOKEN');

  /// Токен, заданный владельцем в «Подключениях».
  ///
  /// Развёрнутый движок начал отвечать 401 «устройство не авторизовано», а
  /// токен существовал только как параметр сборки: чтобы вставить его,
  /// требовалась пересборка APK. Токен — такая же оперативная настройка, как
  /// адрес, и меняется он чаще.
  static String _tokenOverride = '';

  static String get deviceToken =>
      _tokenOverride.isNotEmpty ? _tokenOverride : compiledDeviceToken;

  static void setDeviceToken(String value) => _tokenOverride = value.trim();

  /// Есть ли настроенный бэкенд.
  static bool get isConfigured => baseUrl.isNotEmpty;

  /// Только HTTPS: гейтвей отдаёт торговые данные и принимает подтверждения.
  static bool get isSecure => baseUrl.startsWith('https://');

  static const requestTimeout = Duration(seconds: 20);
}
