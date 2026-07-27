import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Кольцо конфлюэнс-скора (SignalScore 0–100, ТЗ §5.3).
///
/// В макете — `conic-gradient(#FFD400 score·3,6deg, #26262C 0deg)`: жёлтая дуга
/// от 12 часов по часовой стрелке, остаток — серый. Диаметр 42, внутренний
/// круг 34 (макет v2 §5.1).
class ConfluenceRing extends StatelessWidget {
  const ConfluenceRing({
    super.key,
    required this.score,
    this.size = 42,
    this.innerSize = 34,
    this.innerColor = C.card,
  });

  final int score;
  final double size;
  final double innerSize;

  /// Цвет «дырки» кольца — совпадает с поверхностью, на которой оно лежит.
  final Color innerColor;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: size,
        height: size,
        child: CustomPaint(
          painter: _RingPainter(score: score, innerSize: innerSize, inner: innerColor),
          child: Center(
            child: Text(
              '$score',
              style: T.mono(12, weight: 600, color: C.accent),
            ),
          ),
        ),
      );
}

class _RingPainter extends CustomPainter {
  _RingPainter({required this.score, required this.innerSize, required this.inner});

  final int score;
  final double innerSize;
  final Color inner;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final center = rect.center;
    final radius = size.width / 2;

    canvas.drawCircle(center, radius, Paint()..color = C.borderStrong);
    canvas.drawArc(
      rect,
      -math.pi / 2, // старт с 12 часов
      score * 3.6 * math.pi / 180,
      true,
      Paint()..color = C.accent,
    );
    canvas.drawCircle(center, innerSize / 2, Paint()..color = inner);
  }

  @override
  bool shouldRepaint(_RingPainter old) => old.score != score || old.inner != inner;
}
