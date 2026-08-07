/// Конфигурация подключения к мобильному гейтвею.
///
/// Основной адрес и резервный маршрут можно зафиксировать при сборке:
///
/// ```
/// --dart-define=SIGNALAI_API_BASE_URL=https://api.example.ru
/// --dart-define=SIGNALAI_API_BACKUP_URL=https://relay.example.com
/// ```
///
/// Токен устройства никогда не компилируется в APK: после установки он
/// хранится только в Android Keystore.
abstract final class ApiConfig {
  /// Основной адрес, зафиксированный при сборке.
  static const compiledBaseUrl = String.fromEnvironment('SIGNALAI_API_BASE_URL');

  /// Резервный HTTPS-маршрут к тому же серверному API.
  ///
  /// Он нужен не для балансировки, а для смены сетевого пути: VPN может
  /// поменять DNS/маршрут до российского VPS, пока сам сервер остаётся жив.
  /// Если резерв не задан, клиент повторяет основной маршрут с новым сокетом.
  static const compiledBackupUrl =
      String.fromEnvironment('SIGNALAI_API_BACKUP_URL');

  static String _override = '';
  static String _backupOverride = '';

  static String get baseUrl =>
      _override.isNotEmpty ? _override : compiledBaseUrl;

  static String get backupUrl =>
      _backupOverride.isNotEmpty ? _backupOverride : compiledBackupUrl;

  /// Адрес, заданный владельцем в «Подключениях».
  /// Пустая строка возвращает приложение к адресу из сборки.
  static void setBaseUrl(String value) => _override = value.trim();

  /// Резервный адрес задаётся отдельно. Пустое значение возвращает
  /// сборочный резерв.
  static void setBackupUrl(String value) => _backupOverride = value.trim();

  static bool get isOverridden => _override.isNotEmpty;

  /// Упорядоченный список маршрутов без дублей.
  static List<String> get endpoints {
    final result = <String>[];
    for (final raw in [baseUrl, backupUrl]) {
      final value = raw.trim();
      if (value.isEmpty || result.contains(value)) continue;
      result.add(value);
    }
    return result;
  }

  /// Токен, восстановленный из Android Keystore для текущего изолята.
  static String _runtimeDeviceToken = '';

  static String get deviceToken => _runtimeDeviceToken;

  static void setDeviceToken(String value) =>
      _runtimeDeviceToken = value.trim();

  static bool get isConfigured => baseUrl.isNotEmpty;

  /// Все заданные маршруты должны быть HTTPS. Нельзя сделать резерв менее
  /// защищённым, чем основной путь, только ради доступности.
  static bool get isSecure =>
      endpoints.isNotEmpty && endpoints.every((url) => url.startsWith('https://'));

  static const requestTimeout = Duration(seconds: 20);
}
