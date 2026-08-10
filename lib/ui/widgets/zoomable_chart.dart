import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../domain/models/signal.dart';
import 'trade_chart.dart';

/// Build the chart window that should actually be repainted.
///
/// This is deliberately data-level, not a canvas transform. The returned
/// chart contains only the visible bars, so [TradeChart] recalculates candle
/// spacing and the y-axis from that window. Zone indexes are translated from
/// the original series to the visible slice.
SignalChart sliceSignalChart(
  SignalChart chart, {
  required int start,
  required int bars,
}) {
  final count = chart.candles.length;
  if (count < 2 || bars >= count) return chart;

  final safeBars = bars.clamp(2, count).toInt();
  var safeStart = start.clamp(0, count - 2).toInt();
  var end = math.min(count, safeStart + safeBars);
  if (end - safeStart < safeBars) {
    safeStart = math.max(0, end - safeBars);
  }
  end = math.min(count, safeStart + safeBars);

  final zones = <ChartZone>[];
  for (final zone in chart.zones) {
    // A zone to the right of the visible window has not started yet. A zone
    // that started before the viewport remains active from the left edge.
    if (zone.startIndex >= end) continue;
    zones.add(
      ChartZone(
        from: zone.from,
        to: zone.to,
        startIndex: math.max(0, zone.startIndex - safeStart).toInt(),
        label: zone.label,
      ),
    );
  }

  return SignalChart(
    timeframeLabel: chart.timeframeLabel,
    candles: List<ChartCandle>.unmodifiable(
      chart.candles.sublist(safeStart, end),
    ),
    breakLevel: chart.breakLevel,
    breakLabel: chart.breakLabel,
    zones: List<ChartZone>.unmodifiable(zones),
  );
}

/// Initial horizontal fit by timeframe.
///
/// A single 48-candle default looked acceptable on D1 and H1 but made H4 too
/// compressed on a phone: 48 four-hour bars are eight days of structure in a
/// 294px plot. H4 therefore opens slightly tighter while D1/H1 keep the
/// already-good owner-approved density. Pinch/pan remain continuous after the
/// first frame and double tap returns to this semantic default.
int initialVisibleBars(SignalChart chart) {
  final count = chart.candles.length;
  if (count <= 0) return 0;
  final label = chart.timeframeLabel.trim().toLowerCase();
  final target = switch (label) {
    '4h' || 'h4' => 32,
    '15m' || 'm15' => 64,
    '1h' || 'h1' => 48,
    '1d' || 'd1' => 48,
    _ => 48,
  };
  return math.min(count, target);
}

/// Semantic touch viewport for [TradeChart].
///
/// Pinch changes the number of visible candles around the fingers. Horizontal
/// drag moves the candle window through time. Double tap returns to the latest
/// timeframe-aware default window. The child painter is rebuilt from the
/// selected candles; there is intentionally no [InteractiveViewer] and no
/// bitmap/canvas zoom.
class ZoomableChart extends StatefulWidget {
  const ZoomableChart({super.key, required this.child});

  final Widget child;

  @override
  State<ZoomableChart> createState() => _ZoomableChartState();
}

class _ZoomableChartState extends State<ZoomableChart> {
  static const double _designWidth = TradeChart.viewWidth;
  static const double _plotLeft = 8;
  static const double _plotRight = 302;
  static const int _minimumBars = 8;

  String _signature = '';
  double _start = 0;
  double _visible = 0;

  double _gestureVisible = 0;
  double _anchorIndex = 0;
  bool _gestureScaled = false;

  TradeChart? get _trade =>
      widget.child is TradeChart ? widget.child as TradeChart : null;

  SignalChart? _chartFor(TradeChart trade) => trade.chart ?? trade.signal.chart;

  String _chartSignature(SignalChart chart) {
    final first = chart.candles.isEmpty
        ? ''
        : chart.candles.first.openTime?.toIso8601String() ?? '';
    final last = chart.candles.isEmpty
        ? ''
        : chart.candles.last.openTime?.toIso8601String() ?? '';
    return '${chart.timeframeLabel}:${chart.candles.length}:$first:$last';
  }

  void _ensureViewport(SignalChart chart) {
    final signature = _chartSignature(chart);
    if (_signature == signature) return;
    _signature = signature;
    _resetValues(chart);
  }

  void _resetValues(SignalChart chart) {
    final count = chart.candles.length;
    if (count <= 0) {
      _visible = 0;
      _start = 0;
      return;
    }
    _visible = initialVisibleBars(chart).toDouble();
    _start = math.max(0.0, count - _visible).toDouble();
  }

  void _reset(SignalChart chart) => setState(() => _resetValues(chart));

  double _minVisible(int count) =>
      math.min(count, _minimumBars).toDouble();

  double _clampStart(double value, double visible, int count) {
    final maxStart = math.max(0.0, count - visible).toDouble();
    return value.clamp(0.0, maxStart).toDouble();
  }

  double _plotFraction(Offset local, Size size) {
    if (size.width <= 0) return 0.5;
    final left = size.width * (_plotLeft / _designWidth);
    final right = size.width * (_plotRight / _designWidth);
    final width = math.max(1.0, right - left).toDouble();
    return ((local.dx - left) / width).clamp(0.0, 1.0).toDouble();
  }

  double _plotWidth(Size size) => math.max(
        1.0,
        size.width * ((_plotRight - _plotLeft) / _designWidth),
      ).toDouble();

  void _scaleStart(ScaleStartDetails details, SignalChart chart, Size size) {
    _gestureVisible = _visible;
    final fraction = _plotFraction(details.localFocalPoint, size);
    _anchorIndex = _start + fraction * _visible;
    _gestureScaled = false;
  }

  void _scaleUpdate(ScaleUpdateDetails details, SignalChart chart, Size size) {
    final count = chart.candles.length;
    if (count < 2) return;

    if ((details.scale - 1.0).abs() > 0.002) _gestureScaled = true;

    if (_gestureScaled) {
      final visible = (_gestureVisible / math.max(0.05, details.scale))
          .clamp(_minVisible(count), count.toDouble())
          .toDouble();
      // Anchor the bar under the fingers. Moving the fingers while pinching
      // therefore pans and zooms the time axis in one natural gesture.
      final fractionNow = _plotFraction(details.localFocalPoint, size);
      final start = _clampStart(
        _anchorIndex - fractionNow * visible,
        visible,
        count,
      );
      if ((visible - _visible).abs() > 0.01 ||
          (start - _start).abs() > 0.01) {
        setState(() {
          _visible = visible;
          _start = start;
        });
      }
      return;
    }

    // One finger pans only in time. No canvas translation is applied.
    final deltaBars =
        details.focalPointDelta.dx / _plotWidth(size) * _visible;
    if (deltaBars.abs() < 0.001) return;
    final start = _clampStart(_start - deltaBars, _visible, count);
    if ((start - _start).abs() > 0.001) {
      setState(() => _start = start);
    }
  }

  SignalChart _visibleChart(SignalChart chart) {
    final bars = _visible.round().clamp(2, chart.candles.length).toInt();
    final start = _start.floor();
    return sliceSignalChart(chart, start: start, bars: bars);
  }

  TradeChart _rebuildTrade(TradeChart source, SignalChart chart) => TradeChart(
        signal: source.signal,
        chart: chart,
        bornAt: source.bornAt,
        annotations: source.annotations,
        visibleLayers: source.visibleLayers,
        highlight: source.highlight,
      );

  @override
  Widget build(BuildContext context) {
    final trade = _trade;
    if (trade == null) return widget.child;
    final chart = _chartFor(trade);
    if (chart == null || chart.candles.length < 2) return widget.child;

    _ensureViewport(chart);
    final visibleChart = _visibleChart(chart);

    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(
          constraints.hasBoundedWidth
              ? constraints.maxWidth
              : TradeChart.viewWidth,
          constraints.hasBoundedHeight
              ? constraints.maxHeight
              : TradeChart.viewHeight,
        );
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onDoubleTap: () => _reset(chart),
          onScaleStart: (details) => _scaleStart(details, chart, size),
          onScaleUpdate: (details) => _scaleUpdate(details, chart, size),
          child: _rebuildTrade(trade, visibleChart),
        );
      },
    );
  }
}
