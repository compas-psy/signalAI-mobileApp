import 'package:flutter/widgets.dart';

/// Touch viewport for the custom SignalAI chart.
///
/// Interaction deliberately follows the familiar mobile-chart contract:
/// two-finger pinch zooms around the fingers, one-finger drag pans the zoomed
/// chart, and double tap resets the viewport. The chart itself remains our
/// audited CustomPainter; only its viewport is transformed.
class ZoomableChart extends StatefulWidget {
  const ZoomableChart({super.key, required this.child});

  final Widget child;

  @override
  State<ZoomableChart> createState() => _ZoomableChartState();
}

class _ZoomableChartState extends State<ZoomableChart> {
  final TransformationController _transform = TransformationController();

  @override
  void dispose() {
    _transform.dispose();
    super.dispose();
  }

  void _reset() {
    final identity = _transform.value.clone()..setIdentity();
    setState(() => _transform.value = identity);
  }

  @override
  Widget build(BuildContext context) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onDoubleTap: _reset,
        child: InteractiveViewer(
          transformationController: _transform,
          minScale: 1,
          maxScale: 8,
          panEnabled: true,
          scaleEnabled: true,
          panAxis: PanAxis.free,
          boundaryMargin: const EdgeInsets.all(160),
          clipBehavior: Clip.hardEdge,
          child: widget.child,
        ),
      );
}
