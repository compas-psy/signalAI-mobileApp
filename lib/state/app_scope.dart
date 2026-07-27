import 'package:flutter/widgets.dart';

import 'app_controller.dart';

/// Доступ к [AppController] из любого места дерева виджетов.
class AppScope extends InheritedNotifier<AppController> {
  const AppScope({super.key, required AppController controller, required super.child})
      : super(notifier: controller);

  static AppController of(BuildContext context) =>
      _require(context.dependOnInheritedWidgetOfExactType<AppScope>());

  /// Чтение без подписки на обновления — для обработчиков нажатий.
  static AppController read(BuildContext context) =>
      _require(context.getInheritedWidgetOfExactType<AppScope>());

  /// Отсутствие [AppScope] — ошибка программиста, и она обязана назвать себя.
  ///
  /// Раньше здесь стоял `assert` плюс `!`. В релизе `assert` вырезается, и
  /// вместо сообщения происходил null-check: Flutter показывал ErrorWidget,
  /// то есть просто серый прямоугольник без единого слова. Дефект дожил до
  /// устройства именно поэтому.
  static AppController _require(AppScope? scope) {
    final controller = scope?.notifier;
    if (controller == null) {
      throw FlutterError(
        'AppScope не найден в дереве виджетов.\n'
        'Так бывает, когда экран открыт через Navigator.push из ветки, где '
        'AppScope стоит ниже Navigator: маршрут встаёт рядом с ним, а не под '
        'ним. AppScope должен быть выше MaterialApp — см. lib/main.dart.',
      );
    }
    return controller;
  }
}
