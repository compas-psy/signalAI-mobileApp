import 'package:flutter/widgets.dart';

import 'svg_path.dart';

/// Окружность в составе иконки.
class IconCircle {
  const IconCircle(this.cx, this.cy, this.r, {this.fill});

  final double cx;
  final double cy;
  final double r;

  /// Если задан — окружность заливается этим цветом (у иконки «Стратегии»
  /// кружки закрашены фоном, чтобы «прорезать» линии).
  final Color? fill;
}

/// Иконка, заданная путями SVG в системе координат 24×24 — ровно так, как она
/// записана в макете.
class IconSpec {
  const IconSpec({
    this.paths = const [],
    this.circles = const [],
    this.strokeWidth = 2,
  });

  final List<String> paths;
  final List<IconCircle> circles;
  final double strokeWidth;
}

/// Набор иконок из макета `design/SignalAI App.dc.html`.
abstract final class Icons {
  static const bell = IconSpec(
    paths: [
      'M6 9a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6',
      'M10 19a2 2 0 0 0 4 0',
    ],
    strokeWidth: 1.8,
  );

  static const chevronLeft = IconSpec(paths: ['M15 18l-6-6 6-6']);

  static const shield = IconSpec(
    paths: ['M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z'],
  );

  static const info = IconSpec(
    paths: ['M12 9v4M12 17h.01'],
    circles: [IconCircle(12, 12, 10)],
  );

  static const check = IconSpec(paths: ['M20 6L9 17l-5-5'], strokeWidth: 2.4);

  static const navIdeas = IconSpec(paths: ['M13 2L4 14h6l-1 8 9-12h-6l1-8z']);

  static const navTrades = IconSpec(paths: ['M3 17l5-6 4 3 6-8', 'M21 3v18H3']);

  static IconSpec navStrategies(Color cutout) => IconSpec(
        paths: const ['M4 6h16M4 12h16M4 18h16'],
        circles: [
          IconCircle(9, 6, 2, fill: cutout),
          IconCircle(15, 12, 2, fill: cutout),
          IconCircle(7, 18, 2, fill: cutout),
        ],
      );

  static const navSettings = IconSpec(
    paths: [
      'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 '
          '1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 '
          '1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 '
          '1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 '
          '0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 '
          '2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
    ],
    circles: [IconCircle(12, 12, 3)],
  );
}

/// Отрисовка [IconSpec] заданным цветом и размером.
class VectorIcon extends StatelessWidget {
  const VectorIcon(this.spec, {super.key, required this.size, required this.color});

  final IconSpec spec;
  final double size;
  final Color color;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: size,
        height: size,
        child: CustomPaint(painter: _IconPainter(spec, color)),
      );
}

class _IconPainter extends CustomPainter {
  _IconPainter(this.spec, this.color);

  final IconSpec spec;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 24, size.height / 24);

    final stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = spec.strokeWidth
      ..color = color
      // Значения по умолчанию SVG: butt-cap и miter-join — в макете
      // stroke-linecap/linejoin не переопределены.
      ..strokeCap = StrokeCap.butt
      ..strokeJoin = StrokeJoin.miter;

    for (final d in spec.paths) {
      canvas.drawPath(parseSvgPath(d), stroke);
    }
    for (final c in spec.circles) {
      final center = Offset(c.cx, c.cy);
      if (c.fill != null) {
        canvas.drawCircle(center, c.r, Paint()..color = c.fill!);
      } else {
        canvas.drawCircle(center, c.r, stroke);
      }
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_IconPainter old) => old.color != color || old.spec != spec;
}
