import 'package:flutter/widgets.dart';

/// Разметка под ширину экрана: телефон, планшет, планшет в альбоме.
///
/// Пороги — стандартные для Material 3 (600/900 dp), потому что они совпадают
/// с реальными классами устройств, а не подобраны под один планшет: 600 —
/// граница «телефон / небольшой планшет», 900 — ширина, на которой две
/// колонки перестают быть теснотой и становятся выигрышем.
enum Pane {
  /// Телефон в портрете: одна колонка, нижняя навигация.
  compact,

  /// Планшет в портрете или телефон в альбоме: одна колонка с полями,
  /// навигация сбоку — большой палец до низа экрана уже не дотягивается.
  medium,

  /// Планшет в альбоме: две колонки — список и разбор рядом.
  expanded;

  static Pane of(double width) {
    if (width >= 900) return Pane.expanded;
    if (width >= 600) return Pane.medium;
    return Pane.compact;
  }

  bool get isCompact => this == Pane.compact;

  /// Навигация уезжает вбок: на широком экране нижняя панель — это полоса
  /// длиной в пол-метра, до краёв которой неудобно тянуться.
  bool get usesSideNav => this != Pane.compact;

  /// Две колонки: слева список идей, справа разбор выбранной.
  bool get usesTwoPane => this == Pane.expanded;
}

/// Максимальная ширина колонки с содержимым.
///
/// Строка длиной в 1200 пикселей читается плохо независимо от того, сколько
/// места есть: глаз теряет начало следующей строки. Колонка ограничивается и
/// центрируется — так же, как это делает любой профессиональный терминал на
/// широком мониторе.
class ReadableColumn extends StatelessWidget {
  const ReadableColumn({super.key, required this.child, this.maxWidth = 720});

  final Widget child;
  final double maxWidth;

  @override
  Widget build(BuildContext context) => Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: maxWidth),
          child: child,
        ),
      );
}
