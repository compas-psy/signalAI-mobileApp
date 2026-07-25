import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Кольцо конфлюэнс-скора (SignalScore 0–100, ТЗ §5.3).
///
/// В макете — `conic-gradient(#FFD400 score·3,6deg, #26262C 0deg)`: жёлтая дуга
/// от 12 часов по часовой стрелке, остаток — серый.
class ConfluenceRing extends StatelessWidget {
  const ConfluenceRing({super.key, required this.score, this.size = 44, this.innerSize = 35});

  final int score;
  final double size;
  final double innerSize;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: size,
        height: size,
        child: CustomPaint(
          painter: _RingPainter(score: score, innerSize: innerSize),
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
  _RingPainter({required this.score, required this.innerSize});

  final int score;
  final double innerSize;

  @override
  void paint(Canvas canvas, Size size) {
    final rect = Offset.zero & size;
    final center = rect.center;
    final radius = size.width / 2;

    canvas.drawCircle(center, radius, Paint()..color = C.border);
    canvas.drawArc(
      rect,
      -math.pi / 2, // старт с 12 часов
      score * 3.6 * math.pi / 180,
      true,
      Paint()..color = C.accent,
    );
    canvas.drawCircle(center, innerSize / 2, Paint()..color = C.card);
  }

  @override
  bool shouldRepaint(_RingPainter old) => old.score != score;
}
