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

  /// Предел ширины содержимого раздела.
  ///
  /// На телефоне предела нет; на планшете колонка ограничена, иначе строка
  /// растягивается на всю ширину и глаз теряет начало следующей. В альбоме
  /// предел выше: там помещаются две колонки карточек.
  double get contentWidth => switch (this) {
        Pane.compact => double.infinity,
        Pane.medium => 760,
        Pane.expanded => 1160,
      };
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

/// Карточки раздела: одна колонка на телефоне, две — когда хватает ширины.
///
/// Разделы «Инвест», «Сделки», «Стратегии» и «Настройки» — это ленты карточек.
/// На планшете лента в одну колонку оставляет половину экрана пустой, а сама
/// становится вдвое длиннее, чем нужно. Порядок сохраняется по колонкам слева
/// направо: первая карточка (статус раздела) всегда наверху слева.
class CardGrid extends StatelessWidget {
  const CardGrid({
    super.key,
    required this.children,
    this.gap = 12,
    this.breakpoint = 700,
  });

  final List<Widget> children;
  final double gap;

  /// Ширина, начиная с которой колонок становится две.
  final double breakpoint;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth < breakpoint) return _column(children);

          // Раскладка змейкой по строкам, а не «первая половина слева»:
          // соседние по смыслу карточки остаются рядом по вертикали.
          final left = <Widget>[];
          final right = <Widget>[];
          for (var i = 0; i < children.length; i++) {
            (i.isEven ? left : right).add(children[i]);
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: _column(left)),
              SizedBox(width: gap),
              Expanded(child: _column(right)),
            ],
          );
        },
      );

  Widget _column(List<Widget> items) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (var i = 0; i < items.length; i++) ...[
            if (i > 0) SizedBox(height: gap),
            items[i],
          ],
        ],
      );
}
