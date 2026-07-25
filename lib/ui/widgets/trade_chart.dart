import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';

/// График сделки в стиле TradingView.
///
/// Порт SVG-графика из макета (`design/SignalAI App.dc.html`, метод `chart()`):
/// свечи, диапазон Вайкоффа, ордер-блок, BOS/CHoCH, spring/UTAD, зоны риска и
/// профита, линии входа/SL/TP и ценовые чипы на шкале.
///
/// Система координат исходника — 380×292; здесь она масштабируется под ширину
/// контейнера, поэтому пропорции и все отступы сохраняются точно.
class TradeChart extends StatefulWidget {
  const TradeChart({super.key, required this.signal});

  final TradingSignal signal;

  static const double viewWidth = 380;
  static const double viewHeight = 292;

  @override
  State<TradeChart> createState() => _TradeChartState();
}

class _TradeChartState extends State<TradeChart> with SingleTickerProviderStateMixin {
  late final AnimationController _pulse = AnimationController(
    vsync: this,
    // @keyframes pulseDot: 1.6s, opacity 1 → .35 → 1
    duration: const Duration(milliseconds: 1600),
  )..repeat();

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AspectRatio(
        aspectRatio: TradeChart.viewWidth / TradeChart.viewHeight,
        child: RepaintBoundary(
          child: AnimatedBuilder(
            animation: _pulse,
            builder: (context, _) => CustomPaint(
              painter: _ChartPainter(
                signal: widget.signal,
                // 0 → 1 → 0: opacity 1 в начале и конце, .35 в середине
                pulse: 1 - 0.65 * (1 - (2 * _pulse.value - 1).abs()),
              ),
              size: Size.infinite,
            ),
          ),
        ),
      );
}

/// Одна свеча в единицах риска (R) относительно цены входа.
class _Candle {
  const _Candle(this.open, this.close, this.high, this.low);

  final double open;
  final double close;
  final double high;
  final double low;
}

class _ChartPainter extends CustomPainter {
  _ChartPainter({required this.signal, required this.pulse});

  final TradingSignal signal;
  final double pulse;

  // Геометрия исходного SVG.
  static const double _w = TradeChart.viewWidth;
  static const double _h = TradeChart.viewHeight;
  static const double _padTop = 12;
  static const double _padBottom = 16;
  static const double _plotRight = 302;
  static const double _axisX = 308;
  static const int _candleCount = 44;

  /// Опорные точки ломаной цены: [индекс свечи, цена в R].
  static const List<List<double>> _anchors = [
    [0, 1.95], [9, .5], [12, .12], [16, .58], [20, .08], [24, .5],
    [25, -.5], [26, .12], [30, .6], [33, 1.12], [36, .55], [40, .02], [43, .14],
  ];

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / _w, size.height / _h);
    canvas.clipRect(const Rect.fromLTWH(0, 0, _w, _h));

    final risk = signal.riskPerUnit;
    final direction = signal.direction.isLong ? 1 : -1;
    // Цена, соответствующая уровню u (в единицах риска).
    double price(double u) => signal.entry + direction * u * risk;
    final tpLevels = [
      for (final tp in signal.takeProfits) (tp.price - signal.entry).abs() / risk,
    ];

    final candles = _buildCandles();

    var uMin = -1.35;
    var uMax = (tpLevels.isEmpty ? 1.0 : tpLevels.last) + .4;
    for (final c in candles) {
      uMin = math.min(uMin, c.low - .12);
      uMax = math.max(uMax, c.high + .12);
    }
    double y(double u) => _padTop + (uMax - u) / (uMax - uMin) * (_h - _padTop - _padBottom);

    const candleWidth = (216 - 8) / _candleCount;
    double x(num i) => 8 + i * candleWidth + candleWidth / 2;

    String label(double u) => fmt(price(u), signal.priceDecimals);

    // Фон.
    canvas.drawRect(const Rect.fromLTWH(0, 0, _w, _h), Paint()..color = C.inset);

    // Сетка и подписи шкалы.
    final gridPaint = Paint()
      ..color = C.grid
      ..strokeWidth = 1;
    for (var g = 0; g < 5; g++) {
      final u = uMin + (uMax - uMin) * (g + .5) / 5;
      final gy = y(u);
      canvas.drawLine(Offset(6, gy), Offset(_plotRight, gy), gridPaint);
      _text(canvas, label(u), Offset(_axisX, gy + 3), size: 8.5, color: C.axis, mono: true);
    }

    // Диапазон Вайкоффа.
    final wyckoffRect = _rect(x(10) - candleWidth, y(.72), x(30) - x(10) + candleWidth, y(-.08) - y(.72));
    canvas.drawRRect(
      RRect.fromRectAndRadius(wyckoffRect, const Radius.circular(2)),
      Paint()..color = const Color.fromRGBO(142, 142, 152, .05),
    );
    _dashed(
      canvas,
      Path()..addRRect(RRect.fromRectAndRadius(wyckoffRect, const Radius.circular(2))),
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = C.dim,
      dash: 4,
      gap: 3,
    );
    _text(
      canvas,
      signal.direction.isLong
          ? 'Диапазон Вайкоффа · фаза C'
          : 'Диапазон Вайкоффа · распределение',
      Offset(x(10) - candleWidth, y(.72) - 5),
      size: 8.5,
      color: C.muted,
      weight: 600,
    );

    // Ордер-блок.
    final obRect = _rect(x(30), y(.12), _plotRight - x(30), y(-.35) - y(.12));
    canvas.drawRRect(
      RRect.fromRectAndRadius(obRect, const Radius.circular(2)),
      Paint()..color = const Color.fromRGBO(255, 212, 0, .09),
    );
    _dashed(
      canvas,
      Path()..addRRect(RRect.fromRectAndRadius(obRect, const Radius.circular(2))),
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = const Color.fromRGBO(255, 212, 0, .45),
      dash: 3,
      gap: 3,
    );
    _text(canvas, 'OB 1H', Offset(x(30) + 4, y(-.35) - 4),
        size: 8.5, color: C.accent, weight: 700);

    // Слом структуры.
    _dashedLine(
      canvas,
      Offset(x(13), y(.72)),
      Offset(x(34), y(.72)),
      Paint()
        ..color = C.muted
        ..strokeWidth = 1,
      dash: 2,
      gap: 3,
    );
    _text(
      canvas,
      signal.direction.isLong ? 'BOS ↑' : 'CHoCH ↓',
      Offset(x(34) + 2, y(.72) + 11),
      size: 8.5,
      color: C.textSecondary,
      weight: 700,
    );

    // Spring / UTAD.
    _text(
      canvas,
      signal.direction.isLong ? 'Spring' : 'UTAD',
      Offset(x(25) - 12, y(-.78) + 13),
      size: 8.5,
      color: C.accent,
      weight: 700,
    );

    // Инструмент позиции: зоны риска и профита, линии входа, стопа и тейков.
    final toolLeft = x(43) + 8;
    canvas.drawRect(
      _rect(toolLeft, y(0), _plotRight - toolLeft, y(-1) - y(0)),
      Paint()..color = const Color.fromRGBO(255, 92, 92, .10),
    );
    if (tpLevels.isNotEmpty) {
      canvas.drawRect(
        _rect(toolLeft, y(tpLevels.last), _plotRight - toolLeft, y(0) - y(tpLevels.last)),
        Paint()..color = const Color.fromRGBO(47, 213, 117, .08),
      );
    }
    _dashedLine(
      canvas,
      Offset(x(28), y(0)),
      Offset(_plotRight, y(0)),
      Paint()
        ..color = C.accent
        ..strokeWidth = 1.2,
      dash: 5,
      gap: 3,
    );
    _dashedLine(
      canvas,
      Offset(toolLeft - 30, y(-1)),
      Offset(_plotRight, y(-1)),
      Paint()
        ..color = C.red
        ..strokeWidth = 1,
      dash: 4,
      gap: 3,
    );
    for (var k = 0; k < tpLevels.length; k++) {
      final ty = y(tpLevels[k]);
      _dashedLine(
        canvas,
        Offset(toolLeft - 4, ty),
        Offset(_plotRight, ty),
        Paint()
          ..color = C.green.withValues(alpha: .85)
          ..strokeWidth = 1,
        dash: 3,
        gap: 3,
      );
      _text(canvas, 'TP${k + 1}', Offset(toolLeft, ty - 3),
          size: 8, color: C.green, mono: true);
    }

    // Свечи.
    for (var i = 0; i < candles.length; i++) {
      final candle = candles[i];
      final up = candle.close >= candle.open;
      final paint = Paint()
        ..color = up ? C.green : C.red
        ..strokeWidth = 1;
      final cx = x(i);
      canvas.drawLine(Offset(cx, y(candle.high)), Offset(cx, y(candle.low)), paint);
      final top = y(math.max(candle.open, candle.close));
      final bottom = y(math.min(candle.open, candle.close));
      canvas.drawRect(
        Rect.fromLTWH(cx - 1.7, top, 3.4, math.max(1, bottom - top)),
        paint,
      );
    }

    // Маркер текущей цены — пульсирует, как в макете.
    canvas.drawCircle(
      Offset(x(_candleCount - 1), y(candles.last.close)),
      3.4,
      Paint()..color = C.accent.withValues(alpha: pulse),
    );

    // Ценовые чипы на шкале: стоп, тейки, вход.
    void chip(double u, Color background, Color foreground) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(_axisX - 6, y(u) - 7.5, 80, 15),
          const Radius.circular(3),
        ),
        Paint()..color = background,
      );
      _text(canvas, label(u), Offset(_axisX, y(u) + 3.5),
          size: 9, color: foreground, weight: 600, mono: true);
    }

    chip(-1, C.chipSl, const Color(0xFFFFFFFF));
    for (final level in tpLevels) {
      chip(level, C.chipTp, C.chipTpText);
    }
    chip(0, C.accent, C.onAccent);

    canvas.restore();
  }

  /// Свечи строятся детерминированно из опорных точек — те же формулы,
  /// что в макете, поэтому картинка совпадает свеча в свечу.
  List<_Candle> _buildCandles() {
    final closes = <double>[];
    for (var i = 0; i < _candleCount; i++) {
      var j = 0;
      while (j < _anchors.length - 2 && _anchors[j + 1][0] < i) {
        j++;
      }
      final x0 = _anchors[j][0], y0 = _anchors[j][1];
      final x1 = _anchors[j + 1][0], y1 = _anchors[j + 1][1];
      final t = math.min(1.0, math.max(0.0, (i - x0) / math.max(1, x1 - x0)));
      closes.add(y0 + (y1 - y0) * t + math.sin(i * 3.7) * .05);
    }

    return [
      for (var i = 0; i < closes.length; i++)
        () {
          final close = closes[i];
          final open = i > 0 ? closes[i - 1] : close + .12;
          final wick = .05 + (math.sin(i * 2.3)).abs() * .09;
          var high = math.max(open, close) + wick;
          var low = math.min(open, close) - wick;
          // Хвосты spring и кульминации — заданы явно.
          if (i == 25) low = -.78;
          if (i == 33) high = 1.22;
          return _Candle(open, close, high, low);
        }(),
    ];
  }

  /// Прямоугольник по левому-верхнему углу и размерам (высота может быть
  /// отрицательной, как в SVG-координатах макета).
  Rect _rect(double left, double top, double width, double height) =>
      Rect.fromLTWH(left, top, width, height);

  void _dashedLine(Canvas canvas, Offset from, Offset to, Paint paint,
      {required double dash, required double gap}) {
    _dashed(canvas, Path()..addPolygon([from, to], false), paint, dash: dash, gap: gap);
  }

  /// Пунктир: во Flutter нет аналога stroke-dasharray, поэтому режем путь.
  void _dashed(Canvas canvas, Path path, Paint paint,
      {required double dash, required double gap}) {
    final stroked = Paint()
      ..color = paint.color
      ..strokeWidth = paint.strokeWidth
      ..style = PaintingStyle.stroke;
    for (final metric in path.computeMetrics()) {
      var distance = 0.0;
      while (distance < metric.length) {
        final next = math.min(distance + dash, metric.length);
        canvas.drawPath(metric.extractPath(distance, next), stroked);
        distance = next + gap;
      }
    }
  }

  /// Текст с привязкой к базовой линии — как атрибут `y` в SVG.
  void _text(
    Canvas canvas,
    String text,
    Offset baselineStart, {
    required double size,
    required Color color,
    int weight = 400,
    bool mono = false,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          fontFamily: mono ? 'JetBrains Mono' : 'Manrope',
          fontSize: size,
          fontWeight: switch (weight) {
            600 => FontWeight.w600,
            700 => FontWeight.w700,
            _ => FontWeight.w400,
          },
          fontVariations: [FontVariation('wght', weight.toDouble())],
          color: color,
          height: 1,
        ),
      ),
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.left,
    )..layout();
    final baseline = painter.computeDistanceToActualBaseline(TextBaseline.alphabetic);
    painter.paint(canvas, Offset(baselineStart.dx, baselineStart.dy - baseline));
  }

  @override
  bool shouldRepaint(_ChartPainter old) => old.signal.id != signal.id || old.pulse != pulse;
}
