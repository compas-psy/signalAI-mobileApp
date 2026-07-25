import 'package:flutter/widgets.dart';

import 'app_controller.dart';

/// Доступ к [AppController] из любого места дерева виджетов.
class AppScope extends InheritedNotifier<AppController> {
  const AppScope({super.key, required AppController controller, required super.child})
      : super(notifier: controller);

  static AppController of(BuildContext context) {
    final scope = context.dependOnInheritedWidgetOfExactType<AppScope>();
    assert(scope?.notifier != null, 'AppScope не найден в дереве виджетов');
    return scope!.notifier!;
  }

  /// Чтение без подписки на обновления — для обработчиков нажатий.
  static AppController read(BuildContext context) {
    final scope = context.getInheritedWidgetOfExactType<AppScope>();
    assert(scope?.notifier != null, 'AppScope не найден в дереве виджетов');
    return scope!.notifier!;
  }
}
