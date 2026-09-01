import 'dart:async';

import 'package:flutter/widgets.dart';

import '../../data/api/live_idea_source.dart';
import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'trade_chart.dart';
import 'zoomable_chart.dart';

/// График идеи с двумя слоями времени.
///
/// Разметка/уровни остаются снимком исходной идеи, а свечи поверх них
/// обновляются с сервера. Поэтому через два часа владелец видит не картинку
/// момента рождения идеи, а то, что рынок сделал с этим планом после сигнала.
class IdeaChartCard extends StatefulWidget {
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
    this.liveSource,
    this.onProgress,
  });

  final TradingSignal signal;
  final Idea? idea;
  final String timeframe;
  final ValueChanged<String>? onTimeframe;
  final bool loading;
  final bool failed;
  final String failureReason;
  final LiveIdeaSource? liveSource;
  final SignalChart? chart;
  final Set<ChartLayer> available;
  final Set<ChartLayer> visible;
  final Set<String> highlight;
  final ValueChanged<ChartLayer> onToggle;

  /// Свежий путь рынка нужен не только графику: родитель использует тот же
  /// серверный verdict, чтобы не показывать действие после `ВХОД ПОЗДНИЙ`.
  /// null намеренно не публикуется при сетевой ошибке — временный отказ
  /// источника не должен снимать уже установленный запрет на новый вход.
  final ValueChanged<IdeaMarketProgress>? onProgress;

  @override
  State<IdeaChartCard> createState() => _IdeaChartCardState();
}

class _IdeaChartCardState extends State<IdeaChartCard> {
  final LiveIdeaSource _defaultLiveSource = LiveIdeaSource();
  LiveIdeaSource get _liveSource => widget.liveSource ?? _defaultLiveSource;
  LiveIdeaData? _live;
  Timer? _refreshTimer;
  bool _liveLoading = false;

  @override
  void initState() {
    super.initState();
    _scheduleImmediate();
    _refreshTimer = Timer.periodic(
      const Duration(minutes: 1),
      (_) => _refreshLive(),
    );
  }

  @override
  void didUpdateWidget(IdeaChartCard oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.idea?.id != widget.idea?.id ||
        oldWidget.timeframe != widget.timeframe) {
      _live = null;
      _scheduleImmediate();
    }
  }

  void _scheduleImmediate() {
    if (widget.idea == null) return;
    WidgetsBinding.instance.addPostFrameCallback((_) => _refreshLive());
  }

  String _liveTimeframe() => widget.timeframe.isEmpty
      ? (widget.chart?.timeframeLabel ?? '1h')
      : widget.timeframe;

  Future<void> _refreshLive() async {
    final idea = widget.idea;
    if (idea == null || _liveLoading) return;
    final requestedTimeframe = _liveTimeframe();
    _liveLoading = true;
    try {
      final result = await _liveSource.load(
        idea,
        timeframe: requestedTimeframe,
      );
      if (!mounted ||
          widget.idea?.id != idea.id ||
          _liveTimeframe() != requestedTimeframe) {
        return;
      }
      setState(() => _live = result);
      final progress = result.progress;
      if (progress != null) widget.onProgress?.call(progress);
    } finally {
      _liveLoading = false;
      // A timeframe/idea switch can happen while the previous HTTP request is
      // in flight. didUpdateWidget already asked for a refresh, but that call
      // may have returned early because _liveLoading was still true. Queue one
      // more request for the current key so a late 1h response can never leave
      // the 4h tab without its own fetch.
      if (mounted &&
          (widget.idea?.id != idea.id ||
              _liveTimeframe() != requestedTimeframe)) {
        _scheduleImmediate();
      }
    }
  }

  @override
  void dispose() {
    _refreshTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final idea = widget.idea;
    final layers = [
      for (final layer in ChartLayer.values)
        if (widget.available.contains(layer)) layer,
    ];
    final liveChart = _live?.chart;
    final drawn = liveChart ?? widget.chart ?? widget.signal.chart;
    final hasChart = drawn != null;
    final active = drawn?.timeframeLabel ?? widget.timeframe;
    final loading = widget.loading && !hasChart;
    final showToolbar = hasChart || loading || widget.failed;
    final progress = _live?.progress;

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
              onTimeframe: widget.onTimeframe,
              layers: layers,
              visible: widget.visible,
              onToggle: widget.onToggle,
              loading: loading,
            ),
          Stack(
            children: [
              if (loading)
                _ChartPending('Загружаем свечи $active…')
              else if (widget.failed && !hasChart)
                _ChartPending(
                  'Свечей $active источник не дал'
                  '${widget.failureReason.isEmpty ? '' : ': ${widget.failureReason}'}. '
                  'Разметка и уровни ниже считаны на ${_setupLabel()} и от '
                  'таймфрейма картинки не зависят.',
                  onRetry: () => unawaited(_refreshLive()),
                )
              else
                ZoomableChart(
                  child: TradeChart(
                    signal: widget.signal,
                    chart: drawn,
                    bornAt: idea?.createdAt,
                    annotations: idea?.annotations ?? const [],
                    visibleLayers: widget.visible,
                    highlight: widget.highlight,
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
                      _LegendPill(
                        '${progress?.blocksNewEntry == true ? 'Исходная оценка' : 'Оценка'} '
                        '${idea?.score.value ?? widget.signal.score}',
                      ),
                      ?_rrPill(),
                    ],
                  ),
                ),
              ),
            ],
          ),
          if (progress != null)
            _ProgressStrip(progress: progress, signal: widget.signal),
          if (hasChart)
            Container(
              padding: const EdgeInsets.fromLTRB(12, 9, 12, 11),
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: C.divider)),
              ),
              child: Text(
                _freshnessNote(layers),
                style: T.body(10.5, color: C.faint, height: 1.45),
              ),
            ),
        ],
      ),
    );
  }

  String _freshnessNote(List<ChartLayer> layers) {
    final at = _live?.generatedAt?.toLocal();
    final stamp = at == null
        ? ''
        : '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';
    final live = _live?.liveOverlay == true
        ? 'Рынок обновлён $stamp; формирующаяся свеча показана только для наблюдения и не участвует в генерации сигнала. '
        : 'Показан последний доступный серверный ряд. ';
    return '$live'
        'Уровни и разметка зафиксированы в момент рождения идеи. '
        'Растяните двумя пальцами для масштаба, проведите пальцем для сдвига, '
        'двойной тап — сброс. '
        '${layers.length > 1 ? 'Слои разметки показывают только факторы, вошедшие в исходную оценку. ${_lockedNote(layers)}' : ''}';
  }

  Widget? _rrPill() {
    final plan = widget.idea?.plan;
    if (plan != null) {
      return _LegendPill(
        'R:R ${plan.rrToSecondTarget.toStringAsFixed(1).replaceAll('.', ',')}',
      );
    }
    return widget.signal.riskReward.isEmpty
        ? null
        : _LegendPill('R:R ${widget.signal.riskReward}');
  }

  List<String> _timeframes(String chartTf) {
    final all = [...?widget.idea?.timeframes];
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
    final list = widget.idea?.timeframes ?? const <String>[];
    if (list.isEmpty) return 'сетапном таймфрейме';
    return list.length >= 2 ? list[1] : list.first;
  }
}

class _ProgressStrip extends StatelessWidget {
  const _ProgressStrip({required this.progress, required this.signal});

  final IdeaMarketProgress progress;
  final TradingSignal signal;

  @override
  Widget build(BuildContext context) {
    final color = progress.blocksNewEntry
        ? C.red
        : progress.ambiguous
            ? C.warning
            : progress.tpHitCount > 0 || progress.entryWasAvailable == true
                ? C.green
                : C.info;
    final price = progress.currentPrice;
    final local = progress.asOf.toLocal();
    final stamp = '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    final current = price == null
        ? ''
        : ' · сейчас ${price.toStringAsFixed(signal.priceDecimals).replaceAll('.', ',')}';
    final badge = progress.blocksNewEntry
        ? 'ВХОД ПОЗДНИЙ'
        : progress.entryNowAvailable
            ? 'В ЗОНЕ ВХОДА'
            : progress.entryWasAvailable == true
                ? 'ВХОД БЫЛ'
                : progress.ambiguous
                    ? 'НУЖНА ОГОВОРКА'
                    : 'ЖДЁМ ВХОД';

    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 11),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.08),
        border: const Border(top: BorderSide(color: C.divider)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  'После сигнала · 10m',
                  style: T.body(10, weight: 700, color: C.faint),
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  border: Border.all(color: color.withValues(alpha: 0.35)),
                  borderRadius: BorderRadius.circular(R.pill),
                ),
                child: Text(badge, style: T.mono(9.5, weight: 700, color: color)),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            progress.summary,
            style: T.body(11.5, weight: 650, color: C.text, height: 1.45),
          ),
          const SizedBox(height: 5),
          Text(
            'проверено $stamp$current'
            '${progress.currentBarForming ? ' · текущая 10m свеча ещё формируется' : ''}',
            style: T.mono(9.5, color: C.faint),
          ),
        ],
      ),
    );
  }
}

class _ChartPending extends StatelessWidget {
  const _ChartPending(this.message, {this.onRetry});

  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => AspectRatio(
        aspectRatio: TradeChart.viewWidth / TradeChart.viewHeight,
        child: ColoredBox(
          color: C.inset,
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    message,
                    textAlign: TextAlign.center,
                    style: T.body(11.5, color: C.dim, height: 1.5),
                  ),
                  if (onRetry != null) ...[
                    const SizedBox(height: 12),
                    SizedBox(
                      width: 128,
                      child: ActionButton(
                        label: 'Повторить',
                        onTap: onRetry,
                        dense: true,
                      ),
                    ),
                  ],
                ],
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
            ),
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
