import 'package:flutter/widgets.dart';

import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'trade_chart.dart';

/// График идеи с панелью управления — единый блок, как в прототипе
/// (`chart-card`: тулбар сверху, разметка поверх графика, пояснение снизу).
///
/// Раньше график и переключатели слоёв были двумя отдельными элементами
/// ленты, между ними стоял отступ, и панель читалась как настройка экрана, а
/// не как управление этой картинкой.
class IdeaChartCard extends StatelessWidget {
  const IdeaChartCard({
    super.key,
    required this.signal,
    required this.idea,
    required this.available,
    required this.visible,
    required this.highlight,
    required this.onToggle,
  });

  final TradingSignal signal;
  final Idea? idea;

  /// Слои, за которыми стоит доказательство этой идеи.
  final Set<ChartLayer> available;

  final Set<ChartLayer> visible;
  final Set<String> highlight;
  final ValueChanged<ChartLayer> onToggle;

  @override
  Widget build(BuildContext context) {
    final layers = [
      for (final layer in ChartLayer.values)
        if (available.contains(layer)) layer,
    ];
    final hasChart = signal.chart != null;
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: C.card,
        border: Border.all(color: C.border),
        borderRadius: BorderRadius.circular(R.card),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Без свечей переключать нечего: панель слоёв там была бы
          // управлением без объекта.
          if (hasChart) _Toolbar(
            timeframes: _timeframes(),
            active: signal.chart!.timeframeLabel,
            layers: layers,
            visible: visible,
            onToggle: onToggle,
          ),
          Stack(
            children: [
              TradeChart(
                signal: signal,
                // Разметка §10.6 — та, что нашли детекторы движка.
                annotations: idea?.annotations ?? const [],
                visibleLayers: visible,
                highlight: highlight,
              ),
              // Легенда справа: тег таймфрейма график рисует сам слева, и
              // накладывать на него ещё три пилюли значит спрятать оба.
              Positioned(
                top: 9,
                right: 9,
                child: Wrap(
                  alignment: WrapAlignment.end,
                  spacing: 5,
                  runSpacing: 5,
                  children: [
                    if (idea != null) _LegendPill(idea!.strategy.role),
                    _LegendPill('Оценка ${signal.score}'),
                    if (signal.riskReward.isNotEmpty)
                      _LegendPill('R:R ${signal.riskReward}'),
                  ],
                ),
              ),
            ],
          ),
          if (hasChart && layers.length > 1)
            Container(
              padding: const EdgeInsets.fromLTRB(12, 9, 12, 11),
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: C.divider)),
              ),
              child: Text(
                'Разметка показывает не «всё подряд», а только факторы, '
                'вошедшие в оценку этой идеи. Слой выключается, чтобы '
                'проверить каждую гипотезу отдельно.',
                style: T.body(10.5, color: C.faint, height: 1.45),
              ),
            ),
        ],
      ),
    );
  }

  /// Таймфреймы для переключателя.
  ///
  /// Свечи приходят одним таймфреймом — тем, на котором считался сигнал.
  /// Остальные участвовали в анализе, но графика по ним нет, и рисовать
  /// работающую на вид кнопку, которая ничего не меняет, нельзя.
  List<String> _timeframes() {
    final chartTf = signal.chart?.timeframeLabel ?? '';
    final all = [...?idea?.timeframes];
    if (chartTf.isNotEmpty && !all.contains(chartTf)) all.insert(0, chartTf);
    return all;
  }
}

class _Toolbar extends StatelessWidget {
  const _Toolbar({
    required this.timeframes,
    required this.active,
    required this.layers,
    required this.visible,
    required this.onToggle,
  });

  final List<String> timeframes;
  final String active;
  final List<ChartLayer> layers;
  final Set<ChartLayer> visible;
  final ValueChanged<ChartLayer> onToggle;

  @override
  Widget build(BuildContext context) {
    final tfGroup = timeframes.length < 2
        ? null
        : Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final tf in timeframes) ...[
                if (tf != timeframes.first) const SizedBox(width: 4),
                _TimeframePill(label: tf, active: tf == active),
              ],
            ],
          );
    final layerGroup = layers.length < 2
        ? null
        : Wrap(
            spacing: 5,
            runSpacing: 5,
            children: [
              for (final layer in layers)
                _LayerPill(
                  layer: layer,
                  on: visible.contains(layer),
                  onTap: layer.alwaysOn ? null : () => onToggle(layer),
                ),
            ],
          );
    if (tfGroup == null && layerGroup == null) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.fromLTRB(11, 10, 11, 10),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: C.divider)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (tfGroup != null) ...[
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: tfGroup,
            ),
            if (layerGroup != null) const SizedBox(height: 8),
          ],
          ?layerGroup,
        ],
      ),
    );
  }
}

/// Таймфрейм графика. Активен ровно один — тот, чьи свечи нарисованы.
///
/// Остальные показаны выключенными и не нажимаются: они говорят, на чём
/// считалась идея, а не предлагают переключить картинку.
class _TimeframePill extends StatelessWidget {
  const _TimeframePill({required this.label, required this.active});

  final String label;
  final bool active;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
        decoration: BoxDecoration(
          color: active ? C.segmentActive : null,
          borderRadius: BorderRadius.circular(R.chipLg),
        ),
        child: Text(
          label,
          style: T.mono(11, weight: active ? 600 : 400, color: active ? C.text : C.dim),
        ),
      );
}

class _LayerPill extends StatelessWidget {
  const _LayerPill({required this.layer, required this.on, required this.onTap});

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
        constraints: const BoxConstraints(minHeight: 28),
        alignment: Alignment.center,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: on && !locked ? C.accentFaint : C.inset,
          border: Border.all(color: on && !locked ? C.accentBorder : C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(layer.label, style: T.body(10.5, weight: 700, color: color)),
      ),
    );
  }
}

class _LegendPill extends StatelessWidget {
  const _LegendPill(this.label);

  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: const Color(0xCC0B0B0D),
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(label, style: T.body(10, weight: 700, color: C.textSecondary)),
      );
}
