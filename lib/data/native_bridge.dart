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
