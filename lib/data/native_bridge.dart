import 'package:flutter/services.dart';

/// Мост к нативной стороне (MainActivity.kt).
///
/// Все вызовы деградируют мягко: на платформе без канала (тесты, десктоп)
/// методы возвращают null/false, а не роняют приложение.
class NativeBridge {
  const NativeBridge();

  static const _channel = MethodChannel('ru.signalai.app/native');

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

  /// Читает значение. Применяется только к некритичному — идентификатору
  /// ключа API; сам секрет биржи наружу не отдаётся, см. [vaultSign].
  Future<String?> vaultGet(String name) => _string('vaultGet', {'name': name});

  Future<bool> vaultDelete(String name) => _bool('vaultDelete', {'name': name});

  Future<bool> vaultClear() => _bool('vaultClear');

  /// HMAC-SHA256 сообщения секретом [name], hex. null — секрета нет.
  ///
  /// Подпись считается нативно именно для того, чтобы секрет не попадал в
  /// Dart: его нельзя случайно напечатать в лог или увидеть в дампе.
  Future<String?> vaultSign(String name, String payload) =>
      _string('vaultSign', {'name': name, 'payload': payload});

  /// Есть ли на устройстве чем подтверждать сделку.
  Future<bool> biometricsAvailable() => _bool('biometricsAvailable');

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
  Future<bool> notify({required String title, required String body, int id = 1}) async {
    try {
      return await _channel.invokeMethod<bool>('notify', {
            'title': title,
            'body': body,
            'id': id,
          }) ??
          false;
    } on PlatformException {
      return false;
    } on MissingPluginException {
      return false;
    }
  }
}
