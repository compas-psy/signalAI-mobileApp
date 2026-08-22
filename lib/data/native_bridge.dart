import 'package:flutter/services.dart';

/// Мост к нативной стороне (MainActivity.kt).
///
/// Все вызовы деградируют мягко: на платформе без канала (тесты, десктоп)
/// методы возвращают null/false, а не роняют приложение.
class NativeBridge {
  const NativeBridge();

  static const _channel = MethodChannel('ru.signalai.app/native');

  /// Выданный active-device token движка хранится только в Android Keystore.
  static const engineDeviceTokenKey = 'signalai.engine.device_token';

  /// Каталог файлов приложения для JSON-хранилища. null — канала нет.
  Future<String?> filesDir() async {
    try {
      return await _channel.invokeMethod<String>('filesDir');
    } on PlatformException {
      return null;
    } on MissingPluginException {
      return null;
    }
  }

  /// Версия установленной сборки: «1.0.0 (12)». null — спросить не у кого.
  Future<String?> appVersion() async {
    try {
      return await _channel.invokeMethod<String>('appVersion');
    } on PlatformException {
      return null;
    } on MissingPluginException {
      return null;
    }
  }

  /// Запрос разрешения на уведомления (Android 13+). true — разрешено.
  Future<bool> requestNotificationPermission() async {
    try {
      return await _channel.invokeMethod<bool>('requestNotificationPermission') ?? false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

  /// Запуск фонового контура. [mode] — `persistent` или `burst`.
  Future<bool> monitorStart(String mode) => _bool('monitorStart', {'mode': mode});

  /// Остановка фонового контура вместе с будильником.
  Future<bool> monitorStop() => _bool('monitorStop');

  /// Работает ли контур прямо сейчас.
  Future<bool> monitorRunning() => _bool('monitorRunning');

  /// Доступно ли хранилище секретов Android Keystore.
  Future<bool> vaultAvailable() => _bool('vaultAvailable');

  /// Кладёт секрет. Пустое значение равносильно удалению.
  Future<bool> vaultPut(String name, String value) =>
      _bool('vaultPut', {'name': name, 'value': value});

  Future<bool> vaultHas(String name) => _bool('vaultHas', {'name': name});

  /// Читает значение, необходимое клиентскому API. Биржевые секреты этим
  /// путём не читаются; для их подписи используется [vaultSign].
  Future<String?> vaultGet(String name) => _string('vaultGet', {'name': name});

  Future<bool> vaultDelete(String name) => _bool('vaultDelete', {'name': name});

  Future<bool> vaultClear() => _bool('vaultClear');

  Future<String?> engineDeviceToken() => vaultGet(engineDeviceTokenKey);

  Future<bool> putEngineDeviceToken(String token) =>
      vaultPut(engineDeviceTokenKey, token);

  Future<bool> deleteEngineDeviceToken() =>
      vaultDelete(engineDeviceTokenKey);

  /// HMAC-SHA256 сообщения секретом [name], hex. null — секрета нет.
  ///
  /// Подпись считается нативно именно для того, чтобы секрет не попадал в
  /// Dart: его нельзя случайно напечатать в лог или увидеть в дампе.
  Future<String?> vaultSign(String name, String payload) =>
      _string('vaultSign', {'name': name, 'payload': payload});

  /// Есть ли на устройстве чем подтверждать сделку.
  Future<bool> biometricsAvailable() => _bool('biometricsAvailable');

  /// Чем именно подтверждается сделка: `biometrics`, `credential` или `none`.
  Future<String?> confirmMethod() => _string('confirmMethod');

  /// Сырые факты детекции подтверждения — для диагностики.
  Future<String?> confirmMethodDetails() => _string('confirmMethodDetails');

  /// Системный экран уведомлений приложения — когда диалог разрешения
  /// Android больше не показывает.
  Future<bool> openNotificationSettings() => _bool('notificationSettings');

  /// Адрес из нажатого уведомления — один раз.
  ///
  /// Пустая строка значит «по уведомлению не приходили». Забирается именно
  /// запросом, а не приходит событием: при холодном старте система отдаёт
  /// намерение раньше, чем поднимается движок Flutter, и слушателя ещё нет.
  Future<String> takeLaunchPayload() async {
    try {
      return await _channel.invokeMethod<String>('takeLaunchPayload') ?? '';
    } on PlatformException {
      return '';
    } on MissingPluginException {
      return '';
    }
  }

  /// Диалог подтверждения. false — отказ, отмена или ошибка: при любом
  /// сомнении ордер не уходит.
  Future<bool> biometricConfirm({required String title, String subtitle = ''}) =>
      _bool('biometricConfirm', {'title': title, 'subtitle': subtitle});

  // Любой отказ канала означает одно: этой возможности здесь нет. Кроме
  // PlatformException и MissingPluginException сюда попадает и «binding not
  // initialized» из тестов, где канала нет вовсе. Для моста, у которого
  // контракт «нет платформы — нет функции», это правильная деградация:
  // приложение обязано работать как анализатор и без торгового доступа.
  Future<bool> _bool(String method, [Map<String, dynamic>? args]) async {
    try {
      return await _channel.invokeMethod<bool>(method, args) ?? false;
    } on Object {
      return false;
    }
  }

  Future<String?> _string(String method, [Map<String, dynamic>? args]) async {
    try {
      return await _channel.invokeMethod<String>(method, args);
    } on Object {
      return null;
    }
  }

  /// Локальное уведомление. true — показано (разрешение есть и канал жив).
  ///
  /// [payload] — что открыть по нажатию: идентификатор идеи. Без него пуш
  /// приводит владельца на тот экран, где он был вчера, и заставляет искать
  /// идею руками — то есть перестаёт работать ровно там, ради чего послан.
  /// Идентификатор уведомления по идее.
  ///
  /// Стабильный, а не порядковый. Порядковый означал две беды сразу:
  /// повторное уведомление об одной идее ложится рядом вместо замены, и
  /// снять его потом нечем — связи между идеей и её пушем не существует.
  static int noticeId(String ideaId) {
    if (ideaId.isEmpty) return 500;
    var hash = 7;
    for (final unit in ideaId.codeUnits) {
      hash = (hash * 31 + unit) & 0x7fffffff;
    }
    return 1000 + hash % 9000;
  }

  /// Убрать уведомление из шторки.
  ///
  /// Система хранит показанное, пока его не смахнут руками. Отработавшая
  /// идея продолжает висеть и звать открыть себя — а открывать там нечего.
  /// Врёт при этом не текст, а сам факт присутствия: пуш утверждает, что
  /// есть повод действовать.
  Future<bool> cancelNotice(String ideaId) async {
    try {
      return await _channel.invokeMethod<bool>('cancelNotification', {
            'id': noticeId(ideaId),
          }) ??
          false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }

  Future<bool> notify({
    required String title,
    required String body,
    int id = 1,
    String payload = '',
  }) async {
    try {
      return await _channel.invokeMethod<bool>('notify', {
            'title': title,
            'body': body,
            'id': id,
            'payload': payload,
          }) ??
          false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }
}
