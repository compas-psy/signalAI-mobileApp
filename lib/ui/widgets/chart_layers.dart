import 'package:flutter/widgets.dart';

import '../../domain/idea/evidence.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Переключатели аналитических слоёв графика (ТЗ §9, §10.1).
///
/// Показываются только те слои, за которыми стоит доказательство,
/// участвовавшее в оценке: ТЗ §9.1 прямо запрещает рисовать Wyckoff или SMC,
/// если детектор в оценке не участвовал. Переключатель слоя, который нечем
/// наполнить, — обещание, которого приложение не выполнит.
class ChartLayerBar extends StatelessWidget {
  const ChartLayerBar({
    super.key,
    required this.available,
    required this.visible,
    required this.onToggle,
  });

  /// Слои, за которыми есть доказательства этой идеи.
  final Set<ChartLayer> available;

  /// Включённые сейчас.
  final Set<ChartLayer> visible;

  final ValueChanged<ChartLayer> onToggle;

  @override
  Widget build(BuildContext context) {
    // Порядок фиксирован перечислением: чипы не должны прыгать местами от
    // идеи к идее — глаз ищет знакомое место, а не читает заново.
    final layers = [
      for (final layer in ChartLayer.values)
        if (available.contains(layer)) layer,
    ];
    if (layers.length < 2) return const SizedBox.shrink();

    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          for (final layer in layers) ...[
            if (layer != layers.first) const SizedBox(width: 6),
            _LayerChip(
              layer: layer,
              on: visible.contains(layer),
              onTap: layer.alwaysOn ? null : () => onToggle(layer),
            ),
          ],
        ],
      ),
    );
  }
}

class _LayerChip extends StatelessWidget {
  const _LayerChip({required this.layer, required this.on, required this.onTap});

  final ChartLayer layer;
  final bool on;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    // Слой, который нельзя выключить, выглядит иначе нажимаемого: иначе
    // владелец жмёт по свечам и решает, что чип сломан.
    final locked = onTap == null;
    final color = locked
        ? C.dim
        : on
            ? C.accent
            : C.muted;
    return Pressable(
      onTap: onTap,
      child: Container(
        constraints: const BoxConstraints(minHeight: 32),
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 7),
        decoration: BoxDecoration(
          color: on && !locked ? C.accentFaint : C.card,
          border: Border.all(color: on && !locked ? C.accentBorder : C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(
          layer.label,
          style: T.body(11, weight: 700, color: color),
        ),
      ),
    );
  }
}
