import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../domain/models/signal.dart';
import 'trade_chart.dart';

/// Semantic touch viewport for [TradeChart].
///
/// The previous implementation wrapped the already-painted chart in an
/// [InteractiveViewer]. That merely enlarged pixels: candle width, visible bar
/// count and price autoscale did not change. It looked like zooming a picture.
///
/// This widget changes the *data window* instead. Pinch changes how many bars
/// are passed to [TradeChart], horizontal drag moves that window through time,
/// and [TradeChart] repaints from the selected candles so candle spacing and the
/// vertical price scale are recalculated from the visible range. Double-tap
/// returns to the latest default window.
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
  static const int _defaultBars = 48;
  static const int _minimumBars = 8;

  String _signature = '';
  double _start = 0;
  double _visible = 0;

  double _gestureStart = 0;
  double _gestureVisible = 0;
  double _anchorIndex = 0;
  bool _gestureScaled = false;

  TradeChart? get _trade => widget.child is TradeChart ? widget.child as TradeChart : null;

  SignalChart? _chartFor(TradeChart trade) => trade.chart ?? trade.signal.chart;

  String _chartSignature(SignalChart chart) {
    final first = chart.candles.isEmpty ? '' : chart.candles.first.openTime?.toIso8601String() ?? '';
    final last = chart.candles.isEmpty ? '' : chart.candles.last.openTime?.toIso8601String() ?? '';
    return '${chart.timeframeLabel}:${chart.candles.length}:$first:$last';
  }

  void _ensureViewport(SignalChart chart) {
    final signature = _chartSignature(chart);
    if (_signature == signature) return;
    _signature = signature;
    _resetValues(chart.candles.length);
  }

  void _resetValues(int count) {
    if (count <= 0) {
      _visible = 0;
      _start = 0;
      return;
    }
    _visible = math.min(count, _defaultBars).toDouble();
    _start = math.max(0, count - _visible).toDouble();
  }

  void _reset(int count) {
    setState(() => _resetValues(count));
  }

  double _minVisible(int count) => math.min(count, _minimumBars).toDouble();

  double _clampStart(double value, double visible, int count) {
    final maxStart = math.max(0.0, count - visible);
    return value.clamp(0.0, maxStart).toDouble();
  }

  double _plotFraction(Offset local, Size size) {
    if (size.width <= 0) return 0.5;
    final left = size.width * (_plotLeft / _designWidth);
    final right = size.width * (_plotRight / _designWidth);
    final width = math.max(1.0, right - left);
    return ((local.dx - left) / width).clamp(0.0, 1.0).toDouble();
  }

  double _plotWidth(Size size) =>
      math.max(1.0, size.width * ((_plotRight - _plotLeft) / _designWidth));

  void _scaleStart(ScaleStartDetails details, SignalChart chart, Size size) {
    _gestureStart = _start;
    _gestureVisible = _visible;
    final fraction = _plotFraction(details.localFocalPoint, size);
    _anchorIndex = _start + fraction * _visible;
    _gestureScaled = false;
  }

  void _scaleUpdate(ScaleUpdateDetails details, SignalChart chart, Size size) {
    final count = chart.candles.length;
    if (count < 2) return;

    final scaleChanged = (details.scale - 1.0).abs() > 0.002;
    if (scaleChanged) _gestureScaled = true;

    if (_gestureScaled) {
      final visible = (_gestureVisible / math.max(0.05, details.scale))
          .clamp(_minVisible(count), count.toDouble())
          .toDouble();
      // Keep the candle under the fingers stationary while the number of
      // visible bars changes. This is the interaction users expect from a
      // trading chart, not a bitmap zoom.
      final fractionNow = _plotFraction(details.localFocalPoint, size);
      final start = _clampStart(_anchorIndex - fractionNow * visible, visible, count);
      if ((visible - _visible).abs() > 0.01 || (start - _start).abs() > 0.01) {
        setState(() {
          _visible = visible;
          _start = start;
        });
      }
      return;
    }

    // One-finger movement changes only the time window. Vertical movement is
    // ignored; there is no canvas translation and no y-axis distortion.
    final deltaBars = details.focalPointDelta.dx / _plotWidth(size) * _visible;
    if (deltaBars.abs() < 0.001) return;
    final start = _clampStart(_start - deltaBars, _visible, count);
    if ((start - _start).abs() > 0.001) {
      setState(() => _start = start);
    }
  }

  SignalChart _slice(SignalChart chart) {
    final count = chart.candles.length;
    if (count <= 2 || _visible <= 0 || _visible >= count - 0.01) return chart;

    var start = _start.floor().clamp(0, count - 2);
    var bars = _visible.round().clamp(2, count);
    var end = math.min(count, start + bars);
    if (end - start < bars) {
      start = math.max(0, end - bars);
    }
    end = math.min(count, start + bars);

    final zones = <ChartZone>[];
    for (final zone in chart.zones) {
      // A zone that starts to the right of the visible window has not begun
      // yet and must not be painted. A zone that began before the window is
      // still active and starts at the left edge of the visible viewport.
      if (zone.startIndex >= end) continue;
      zones.add(
        ChartZone(
          from: zone.from,
          to: zone.to,
          startIndex: math.max(0, zone.startIndex - start),
          label: zone.label,
        ),
      );
    }

    return SignalChart(
      timeframeLabel: chart.timeframeLabel,
      candles: List<ChartCandle>.unmodifiable(chart.candles.sublist(start, end)),
      breakLevel: chart.breakLevel,
      breakLabel: chart.breakLabel,
      zones: List<ChartZone>.unmodifiable(zones),
    );
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
    final visibleChart = _slice(chart);

    return LayoutBuilder(
      builder: (context, constraints) {
        final size = Size(
          constraints.hasBoundedWidth ? constraints.maxWidth : TradeChart.viewWidth,
          constraints.hasBoundedHeight ? constraints.maxHeight : TradeChart.viewHeight,
        );
        return GestureDetector(
          behavior: HitTestBehavior.opaque,
          onDoubleTap: () => _reset(chart.candles.length),
          onScaleStart: (details) => _scaleStart(details, chart, size),
          onScaleUpdate: (details) => _scaleUpdate(details, chart, size),
          child: _rebuildTrade(trade, visibleChart),
        );
      },
    );
  }
}
