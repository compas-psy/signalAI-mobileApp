import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Карточка-контейнер: #141419, рамка #1F1F26, скругление 18.
class SectionCard extends StatelessWidget {
  const SectionCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.fromLTRB(15, 13, 15, 13),
    this.margin,
    this.clip = false,
  });

  final Widget child;
  final EdgeInsets padding;
  final EdgeInsets? margin;

  /// Обрезать содержимое по скруглению (для списков с разделителями).
  final bool clip;

  @override
  Widget build(BuildContext context) => Container(
        margin: margin,
        padding: padding,
        clipBehavior: clip ? Clip.antiAlias : Clip.none,
        decoration: BoxDecoration(
          color: C.card,
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.card),
        ),
        child: child,
      );
}

/// Вложенный блок внутри карточки: #101014, скругление 10.
class InsetBox extends StatelessWidget {
  const InsetBox({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    this.radius = R.inset,
  });

  final Widget child;
  final EdgeInsets padding;
  final double radius;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding,
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(radius),
        ),
        child: child,
      );
}

/// Прописной микро-заголовок секции.
class SectionLabel extends StatelessWidget {
  const SectionLabel(this.text, {super.key, this.color = C.muted});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) =>
      Text(text.toUpperCase(), style: T.sectionLabel(color: color));
}

/// Бейдж направления сделки: LONG / SHORT.
class DirectionBadge extends StatelessWidget {
  const DirectionBadge({super.key, required this.label, required this.color, required this.background});

  final String label;
  final Color color;
  final Color background;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.circular(R.chip),
        ),
        child: Text(
          label,
          style: T.body(10, weight: 800, color: color, letterSpacing: 10 * 0.06),
        ),
      );
}

/// Бейдж с рамкой: статус идеи, R:R, «TG ✓».
class OutlineBadge extends StatelessWidget {
  const OutlineBadge({
    super.key,
    required this.label,
    required this.color,
    required this.borderColor,
    this.background,
    this.fontWeight = 400,
    this.padding = const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
    this.radius = R.chip,
  });

  final String label;
  final Color color;
  final Color borderColor;
  final Color? background;
  final int fontWeight;
  final EdgeInsets padding;
  final double radius;

  @override
  Widget build(BuildContext context) => Container(
        padding: padding,
        decoration: BoxDecoration(
          color: background,
          border: Border.all(color: borderColor),
          borderRadius: BorderRadius.circular(radius),
        ),
        child: Text(label, style: T.body(10, weight: fontWeight, color: color)),
      );
}

/// Серый чип-тег сетапа: «Spring», «OB 1H».
class TagChip extends StatelessWidget {
  const TagChip(this.label, {super.key});

  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
        decoration: BoxDecoration(
          color: C.chip,
          borderRadius: BorderRadius.circular(R.chip),
        ),
        child: Text(label, style: T.body(10, color: C.textSecondary)),
      );
}

/// Переключатель 44×24 из макета.
class AppToggle extends StatelessWidget {
  const AppToggle({super.key, required this.value, required this.onChanged});

  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onChanged(!value),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 200),
          width: 44,
          height: 24,
          decoration: BoxDecoration(
            color: value ? C.accent : C.toggleOff,
            borderRadius: BorderRadius.circular(R.pill),
          ),
          child: Stack(
            children: [
              AnimatedPositioned(
                duration: const Duration(milliseconds: 200),
                curve: Curves.easeOut,
                top: 2,
                left: value ? 22 : 2,
                child: Container(
                  width: 20,
                  height: 20,
                  decoration: const BoxDecoration(color: C.bg, shape: BoxShape.circle),
                ),
              ),
            ],
          ),
        ),
      );
}

/// Нажимаемая область с лёгким сжатием — повторяет `style-active` макета.
class Pressable extends StatefulWidget {
  const Pressable({
    super.key,
    required this.child,
    required this.onTap,
    this.pressedScale = 0.99,
  });

  final Widget child;
  final VoidCallback? onTap;
  final double pressedScale;

  @override
  State<Pressable> createState() => _PressableState();
}

class _PressableState extends State<Pressable> {
  bool _pressed = false;

  @override
  Widget build(BuildContext context) {
    final enabled = widget.onTap != null;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: widget.onTap,
      onTapDown: enabled ? (_) => setState(() => _pressed = true) : null,
      onTapUp: enabled ? (_) => setState(() => _pressed = false) : null,
      onTapCancel: enabled ? () => setState(() => _pressed = false) : null,
      child: AnimatedScale(
        scale: _pressed ? widget.pressedScale : 1,
        duration: const Duration(milliseconds: 90),
        child: widget.child,
      ),
    );
  }
}

/// Кнопка действия: заливка для главного действия экрана, контур — для
/// остальных. Собрана в одном месте специально: пока каждый экран рисовал
/// свой контейнер, высота и радиус кнопок разъезжались от экрана к экрану.
class ActionButton extends StatelessWidget {
  const ActionButton({
    super.key,
    required this.label,
    required this.onTap,
    this.primary = false,
    this.color = C.accent,
    this.dense = false,
  });

  final String label;
  final VoidCallback? onTap;

  /// Главное действие экрана — заливка. На экране оно должно быть одно.
  final bool primary;

  final Color color;

  /// Уменьшенная высота для кнопок внутри карточек.
  final bool dense;

  @override
  Widget build(BuildContext context) {
    final enabled = onTap != null;
    final foreground = primary
        ? C.onAccent
        : (enabled ? color : C.faint);
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: EdgeInsets.symmetric(vertical: dense ? 9 : 11),
        decoration: BoxDecoration(
          color: primary ? (enabled ? color : C.inset) : null,
          border: primary
              ? null
              : Border.all(color: enabled ? C.borderHover : C.border),
          borderRadius: BorderRadius.circular(primary ? R.button : R.inner),
        ),
        child: Center(
          child: Text(
            label,
            textAlign: TextAlign.center,
            style: T.body(dense ? 12 : 13, weight: 800, color: foreground),
          ),
        ),
      ),
    );
  }
}

/// Липкая шапка экрана с заголовком (Сделки / Стратегии / Настройки).
class ScreenHeader extends StatelessWidget {
  const ScreenHeader({super.key, required this.title, this.trailing});

  final String title;

  /// Бейдж режима раздела справа от заголовка («ЛОНГИ · 1–3 МЕС»).
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(S.screen, 14, S.screen, 10),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            Expanded(child: Text(title, style: T.jost(23))),
            ?trailing,
          ],
        ),
      );
}

/// Строка «название → значение» с нижней границей.
class KeyValueRow extends StatelessWidget {
  const KeyValueRow({
    super.key,
    required this.name,
    required this.value,
    this.valueStyle,
    this.showDivider = true,
    this.onTap,
  });

  final String name;
  final String value;
  final TextStyle? valueStyle;
  final bool showDivider;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final row = Container(
      padding: const EdgeInsets.symmetric(vertical: 7),
      decoration: showDivider
          ? const BoxDecoration(border: Border(bottom: BorderSide(color: C.divider)))
          : null,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(child: Text(name, style: T.body(12, color: C.muted))),
          const SizedBox(width: 12),
          Flexible(
            child: Text(
              value,
              textAlign: TextAlign.right,
              style: valueStyle ?? T.body(12, weight: 600),
            ),
          ),
        ],
      ),
    );
    return onTap == null ? row : Pressable(onTap: onTap, child: row);
  }
}
