import 'package:flutter/widgets.dart';

import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'trade_chart.dart';
import 'zoomable_chart.dart';

/// График идеи с панелью управления — единый блок, как в прототипе
/// (`chart-card`: тулбар сверху, разметка поверх графика, пояснение снизу).
class IdeaChartCard extends StatelessWidget {
  const IdeaChartCard({
    super.key,
    required this.signal,
    required this.idea,
    required this.available,
    required this.visible,
    required this.highlight,
    required this.onToggle,
    this.chart,
    this.timeframe = '',
    this.onTimeframe,
    this.loading = false,
    this.failed = false,
    this.failureReason = '',
  });

  final TradingSignal signal;
  final Idea? idea;
  final String timeframe;
  final ValueChanged<String>? onTimeframe;
  final bool loading;
  final bool failed;
  final String failureReason;
  final SignalChart? chart;
  final Set<ChartLayer> available;
  final Set<ChartLayer> visible;
  final Set<String> highlight;
  final ValueChanged<ChartLayer> onToggle;

  /// TradeChart historically received the compatibility TradingSignal while
  /// PlanCard showed the immutable server TradePlan. Once the plan was sized,
  /// those could legitimately contain different reference entries (the DOGE
  /// screenshot showed 0.06987 on the chart vs limit 0.06940 in the plan).
  ///
  /// The visible order levels must have one source of truth. Build a display
  /// signal whose prices are the exact signed plan values; all non-price
  /// metadata stays untouched. This changes rendering only — signal discovery
  /// and scoring remain on the server exactly as before.
  TradingSignal get _chartSignal {
    final plan = idea?.plan;
    if (plan == null) return signal;
    return TradingSignal(
      id: signal.id,
      symbol: signal.symbol,
      name: signal.name,
      market: signal.market,
      direction: signal.direction,
      horizon: signal.horizon,
      horizonLabel: signal.horizonLabel,
      score: signal.score,
      // PlanCard labels entryLow as the actual limit/stop-limit order price.
      // Using the midpoint here would still disagree with the number the owner
      // is about to confirm.
      entry: plan.entryLow,
      stopLoss: plan.stop,
      takeProfits: [
        for (var i = 0; i < plan.targets.length; i++)
          TakeProfit(
            index: i + 1,
            price: plan.targets[i].price,
            sharePercent: (plan.targets[i].fraction * 100).round(),
          ),
      ],
      priceDecimals: signal.priceDecimals,
      riskReward: signal.riskReward,
      chips: signal.chips,
      note: signal.note,
      factors: signal.factors,
      events: signal.events,
      unitRisk: signal.unitRisk,
      unitRiskLabel: signal.unitRiskLabel,
      unitMultiplier: signal.unitMultiplier,
      unitDecimals: signal.unitDecimals,
      unitName: signal.unitName,
      lastPrice: signal.lastPrice,
      changeLabel: signal.changeLabel,
      changeUp: signal.changeUp,
      status: signal.status,
      validUntil: signal.validUntil,
      invalidationPrice: signal.invalidationPrice,
      correlationGroup: signal.correlationGroup,
      strategyId: signal.strategyId,
      chart: signal.chart,
      entryIsStop: signal.entryIsStop,
    );
  }

  @override
  Widget build(BuildContext context) {
    final layers = [
      for (final layer in ChartLayer.values)
        if (available.contains(layer)) layer,
    ];
    final drawn = chart ?? signal.chart;
    final hasChart = drawn != null;
    final active = drawn?.timeframeLabel ?? timeframe;
    final showToolbar = hasChart || loading || failed;
    final chartSignal = _chartSignal;
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
          if (showToolbar)
            _Toolbar(
              timeframes: _timeframes(active),
              active: active,
              onTimeframe: onTimeframe,
              layers: layers,
              visible: visible,
              onToggle: onToggle,
              loading: loading,
            ),
          Stack(
            children: [
              if (loading)
                _ChartPending('Загружаем свечи $active…')
              else if (failed && !hasChart)
                _ChartPending(
                  'Свечей $active источник не дал'
                  '${failureReason.isEmpty ? '' : ': $failureReason'}. '
                  'Разметка и уровни ниже считаны на ${_setupLabel()} и от '
                  'таймфрейма картинки не зависят.',
                )
              else
                ZoomableChart(
                  child: TradeChart(
                    signal: chartSignal,
                    chart: chart,
                    bornAt: idea?.createdAt,
                    annotations: idea?.annotations ?? const [],
                    visibleLayers: visible,
                    highlight: highlight,
                  ),
                ),
              Positioned(
                top: 9,
                right: 9,
                child: IgnorePointer(
                  child: Wrap(
                    alignment: WrapAlignment.end,
                    spacing: 5,
                    runSpacing: 5,
                    children: [
                      _LegendPill('Оценка ${idea?.score.value ?? signal.score}'),
                      ?_rrPill(),
                    ],
                  ),
                ),
              ),
            ],
          ),
          if (hasChart)
            Container(
              padding: const EdgeInsets.fromLTRB(12, 9, 12, 11),
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: C.divider)),
              ),
              child: Text(
                'График: растяните двумя пальцами для масштаба, проведите '
                'пальцем для сдвига по времени, двойной тап — сброс. '
                '${layers.length > 1 ? 'Слои разметки показывают только факторы, вошедшие в оценку идеи. ${_lockedNote(layers)}' : ''}',
                style: T.body(10.5, color: C.faint, height: 1.45),
              ),
            ),
        ],
      ),
    );
  }

  Widget? _rrPill() {
    final plan = idea?.plan;
    if (plan != null) {
      return _LegendPill(
        'R:R ${plan.rrToSecondTarget.toStringAsFixed(1).replaceAll('.', ',')}',
      );
    }
    return signal.riskReward.isEmpty ? null : _LegendPill('R:R ${signal.riskReward}');
  }

  List<String> _timeframes(String chartTf) {
    final all = [...?idea?.timeframes];
    if (chartTf.isNotEmpty && !all.contains(chartTf)) all.insert(0, chartTf);
    return all;
  }

  static String _lockedNote(List<ChartLayer> layers) {
    final locked = [for (final l in layers) if (l.alwaysOn) l.label];
    if (locked.isEmpty) return '';
    return locked.length == 1
        ? '«${locked.first}» не выключается: без свечей разметке не на чем стоять.'
        : '${locked.map((l) => '«$l»').join(', ')} не выключаются.';
  }

  String _setupLabel() {
    final list = idea?.timeframes ?? const <String>[];
    if (list.isEmpty) return 'сетапном таймфрейме';
    return list.length >= 2 ? list[1] : list.first;
  }
}

class _ChartPending extends StatelessWidget {
  const _ChartPending(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => AspectRatio(
        aspectRatio: TradeChart.viewWidth / TradeChart.viewHeight,
        child: ColoredBox(
          color: C.inset,
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                message,
                textAlign: TextAlign.center,
                style: T.body(11.5, color: C.dim, height: 1.5),
              ),
            ),
          ),
        ),
      );
}

class _Toolbar extends StatelessWidget {
  const _Toolbar({
    required this.timeframes,
    required this.active,
    required this.layers,
    required this.visible,
    required this.onToggle,
    this.onTimeframe,
    this.loading = false,
  });

  final List<String> timeframes;
  final String active;
  final ValueChanged<String>? onTimeframe;
  final bool loading;
  final List<ChartLayer> layers;
  final Set<ChartLayer> visible;
  final ValueChanged<ChartLayer> onToggle;

  @override
  Widget build(BuildContext context) {
    final switcher = onTimeframe;
    final tfGroup = timeframes.length < 2
        ? null
        : Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final tf in timeframes) ...[
                if (tf != timeframes.first) const SizedBox(width: 4),
                _TimeframePill(
                  label: tf,
                  active: tf == active,
                  onTap: switcher == null || loading || tf == active
                      ? null
                      : () => switcher(tf),
                ),
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

class _TimeframePill extends StatelessWidget {
  const _TimeframePill({
    required this.label,
    required this.active,
    this.onTap,
  });

  final String label;
  final bool active;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
          decoration: BoxDecoration(
            color: active ? C.segmentActive : null,
            borderRadius: BorderRadius.circular(R.chipLg),
          ),
          child: Text(
            label,
            style: T.mono(
              11,
              weight: active ? 600 : 400,
              color: active ? C.text : C.dim,
            ),
          ),
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
    final locked = onTap == null;
    final color = locked
        ? C.dim
        : on
            ? C.accent
            : C.muted;
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
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
