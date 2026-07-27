import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Сегмент-контрол: «План · Факторы · События».
///
/// Макет v2 §5.2.5. Разбор идеи раньше был одной длинной лентой карточек —
/// человек листал мимо того, что ему сейчас не нужно. Три сегмента разводят
/// три разных вопроса: «как вести сделку», «почему она вообще есть»,
/// «что может её сломать».
class SegmentedControl extends StatelessWidget {
  const SegmentedControl({
    super.key,
    required this.items,
    required this.index,
    required this.onSelect,
  });

  final List<String> items;
  final int index;
  final ValueChanged<int> onSelect;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(3),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inner),
        ),
        child: Row(
          children: [
            for (var i = 0; i < items.length; i++)
              Expanded(
                child: GestureDetector(
                  behavior: HitTestBehavior.opaque,
                  onTap: () => onSelect(i),
                  child: AnimatedContainer(
                    duration: const Duration(milliseconds: 140),
                    padding: const EdgeInsets.symmetric(vertical: 9),
                    decoration: BoxDecoration(
                      color: i == index ? C.segmentActive : null,
                      borderRadius: BorderRadius.circular(R.inset - 2),
                    ),
                    child: Center(
                      child: Text(
                        items[i],
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(
                          12,
                          weight: 700,
                          color: i == index ? C.text : C.muted,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      );
}

/// Плитка метрики: подпись сверху, число снизу.
///
/// Ряд из трёх таких заменяет три строки «ключ — значение»: цифры встают в
/// одну линию и сравниваются взглядом, а не чтением.
class MetricTile extends StatelessWidget {
  const MetricTile({
    super.key,
    required this.label,
    required this.value,
    this.color = C.text,
    this.hint,
  });

  final String label;
  final String value;
  final Color color;

  /// Третья строка мелким шрифтом: единица измерения или уточнение.
  final String? hint;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(11, 9, 11, 10),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              label.toUpperCase(),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: T.microLabel(),
            ),
            const SizedBox(height: 4),
            Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: T.mono(13, weight: 600, color: color),
            ),
            if (hint != null) ...[
              const SizedBox(height: 2),
              Text(
                hint!,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: T.body(10, color: C.dim),
              ),
            ],
          ],
        ),
      );
}

/// Ряд плиток одинаковой ширины.
class MetricRow extends StatelessWidget {
  const MetricRow({super.key, required this.tiles});

  final List<MetricTile> tiles;

  // IntrinsicHeight, а не stretch: ряд живёт внутри ListView, где высота не
  // ограничена сверху — плитки должны сравняться по самой высокой из них.
  @override
  Widget build(BuildContext context) => IntrinsicHeight(
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            for (var i = 0; i < tiles.length; i++) ...[
              if (i > 0) const SizedBox(width: 8),
              Expanded(child: tiles[i]),
            ],
          ],
        ),
      );
}

/// Мини-бар силы фактора: три деления, закрашено [strength] из трёх.
///
/// Вес фактора раньше был числом в строке текста; глазом сравнить шесть таких
/// строк невозможно. Три деления читаются мгновенно.
class StrengthBar extends StatelessWidget {
  const StrengthBar({super.key, required this.strength});

  /// 0–3. Ноль — фактор учтён, но ничего не добавил.
  final int strength;

  @override
  Widget build(BuildContext context) => Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          for (var i = 0; i < 3; i++) ...[
            if (i > 0) const SizedBox(width: 3),
            Container(
              width: 12,
              height: 4,
              decoration: BoxDecoration(
                color: switch (strength - i) {
                  >= 2 => C.accent,
                  1 => const Color(0x80FFD400),
                  _ => C.borderStrong,
                },
                borderRadius: BorderRadius.circular(2),
              ),
            ),
          ],
        ],
      );
}
