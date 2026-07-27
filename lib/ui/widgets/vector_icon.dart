import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
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

  static const chevronRight = IconSpec(paths: ['M9 18l6-6-6-6']);

  static const shield = IconSpec(
    paths: ['M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6l8-4z'],
  );

  static const info = IconSpec(
    paths: ['M12 9v4M12 17h.01'],
    circles: [IconCircle(12, 12, 10)],
  );

  static const check = IconSpec(paths: ['M20 6L9 17l-5-5'], strokeWidth: 2.4);

  static const navIdeas = IconSpec(paths: ['M13 2L4 14h6l-1 8 9-12h-6l1-8z']);

  /// «Сегодня» — циферблат: раздел отвечает на вопрос «что сейчас».
  static const navToday = IconSpec(
    paths: ['M12 7v5l3 2'],
    circles: [IconCircle(12, 12, 9)],
  );

  /// «Капитал» — столбики: другой рынок и другой горизонт, чем молния свинга.
  static const navInvest = IconSpec(paths: ['M5 21V10M12 21V4M19 21v-6']);

  static const navTrades = IconSpec(paths: ['M3 17l5-6 4 3 6-8', 'M21 3v18H3']);

  /// «Стратегии» — слайдеры: линии разорваны под ручками, поэтому cutout
  /// закрашивает кружок фоном, на котором иконка лежит.
  static IconSpec navStrategies(Color cutout) => IconSpec(
        paths: const ['M4 7h10M18 7h2M4 12h4M12 12h8M4 17h13'],
        circles: [
          IconCircle(15.5, 7, 2, fill: cutout),
          IconCircle(9.5, 12, 2, fill: cutout),
          IconCircle(18.5, 17, 2, fill: cutout),
        ],
      );

  static const navSettings = IconSpec(
    paths: ['M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1'],
    circles: [IconCircle(12, 12, 3)],
  );

  /// Молния логотипа (viewBox 48×48) — она же передний слой иконки
  /// приложения. Никаких букв: знак должен читаться в 48 dp на рабочем столе.
  static const brandBolt =
      'M26.6 11.5 L16.5 27 h6.9 L21.4 36.5 L31.5 21.6 h-6.9 L26.6 11.5 Z';
}

/// Знак приложения: жёлтая молния в скруглённом квадрате `#17171C`.
///
/// Заменяет растровый `logo.jpg`: вектор не мылится на плотных экранах и
/// совпадает пиксель-в-пиксель с иконкой на рабочем столе.
class BrandMark extends StatelessWidget {
  const BrandMark({super.key, this.size = 30});

  final double size;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: size,
        height: size,
        child: CustomPaint(painter: _BrandPainter()),
      );
}

class _BrandPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / 48, size.height / 48);

    final plate = RRect.fromRectAndRadius(
      const Rect.fromLTWH(1, 1, 46, 46),
      const Radius.circular(13),
    );
    canvas.drawRRect(plate, Paint()..color = C.sheet);
    canvas.drawRRect(
      plate,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = C.borderStrong,
    );
    canvas.drawPath(parseSvgPath(Icons.brandBolt), Paint()..color = C.accent);
    canvas.restore();
  }

  @override
  bool shouldRepaint(_BrandPainter old) => false;
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
