import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Спарклайн эквити и бэктеста — порт метода `spark()` из макета:
/// заливка под линией с прозрачностью .09 и линия толщиной 1.8.
class Sparkline extends StatelessWidget {
  const Sparkline({
    super.key,
    required this.values,
    required this.height,
    required this.color,
    this.emptyLabel,
  });

  final List<double> values;
  final double height;
  final Color color;

  /// Что показать, когда точек меньше двух.
  ///
  /// Кривая по одной точке не строится, и раньше на этом месте оставалась
  /// пустая коробка — молчаливая, неотличимая от поломки. Виджет обязан
  /// сказать, почему он пуст, а не притворяться графиком.
  final String? emptyLabel;

  @override
  Widget build(BuildContext context) {
    if (values.length < 2) {
      return SizedBox(
        height: height,
        width: double.infinity,
        child: Stack(
          alignment: Alignment.center,
          children: [
            // Базовая линия: место графика занято и видно, что оно не пустое
            // по ошибке верстки.
            Align(
              alignment: Alignment.bottomCenter,
              child: Container(height: 1, color: C.border),
            ),
            if (emptyLabel != null)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8),
                child: Text(
                  emptyLabel!,
                  textAlign: TextAlign.center,
                  style: T.body(10.5, color: C.faint, height: 1.4),
                ),
              ),
          ],
        ),
      );
    }
    return SizedBox(
      height: height,
      width: double.infinity,
      child: CustomPaint(painter: _SparklinePainter(values, color)),
    );
  }
}

class _SparklinePainter extends CustomPainter {
  _SparklinePainter(this.values, this.color);

  final List<double> values;
  final Color color;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;

    final min = values.reduce(math.min);
    final max = values.reduce(math.max);
    final span = (max - min) == 0 ? 1 : (max - min);
    final h = size.height;

    final points = <Offset>[
      for (var i = 0; i < values.length; i++)
        Offset(
          i / (values.length - 1) * size.width,
          h - 3 - ((values[i] - min) / span) * (h - 8),
        ),
    ];

    final fill = Path()..moveTo(0, h);
    for (final p in points) {
      fill.lineTo(p.dx, p.dy);
    }
    fill
      ..lineTo(size.width, h)
      ..close();
    canvas.drawPath(fill, Paint()..color = color.withValues(alpha: .09));

    final line = Path()..moveTo(points.first.dx, points.first.dy);
    for (final p in points.skip(1)) {
      line.lineTo(p.dx, p.dy);
    }
    canvas.drawPath(
      line,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.8
        ..strokeJoin = StrokeJoin.round
        ..color = color,
    );
  }

  @override
  bool shouldRepaint(_SparklinePainter old) =>
      old.color != color || !listEquals(old.values, values);

  static bool listEquals(List<double> a, List<double> b) {
    if (a.length != b.length) return false;
    for (var i = 0; i < a.length; i++) {
      if (a[i] != b[i]) return false;
    }
    return true;
  }
}
