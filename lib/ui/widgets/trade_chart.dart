import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// График сделки по реальным свечам сигнала.
///
/// Рисуется только то, что действительно есть в данных: свечи, по которым
/// скринер посчитал идею, уровни входа/SL/TP, реально найденный слом структуры
/// и зоны FVG. Если свечей нет ([TradingSignal.chart] == null) — честная
/// заглушка, а не нарисованная картинка.
///
/// Тег в левом верхнем углу всегда показывает таймфрейм отображаемых свечей.
class TradeChart extends StatefulWidget {
  const TradeChart({super.key, required this.signal});

  final TradingSignal signal;

  static const double viewWidth = 380;
  static const double viewHeight = 292;

  @override
  State<TradeChart> createState() => _TradeChartState();
}

class _TradeChartState extends State<TradeChart> with SingleTickerProviderStateMixin {
  // Создаётся лениво: у заглушки без свечей анимации нет, и заводить для неё
  // тикер (а потом создавать его в dispose через late) незачем.
  AnimationController? _pulse;

  @override
  void dispose() {
    _pulse?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final chart = widget.signal.chart;
    if (chart == null || chart.candles.length < 2) {
      return const _ChartUnavailable();
    }
    final pulse = _pulse ??= AnimationController(
      vsync: this,
      // @keyframes pulseDot из макета: 1.6s, opacity 1 → .35 → 1
      duration: const Duration(milliseconds: 1600),
    )..repeat();
    return AspectRatio(
      aspectRatio: TradeChart.viewWidth / TradeChart.viewHeight,
      child: RepaintBoundary(
        child: AnimatedBuilder(
          animation: pulse,
          builder: (context, _) => CustomPaint(
            painter: _ChartPainter(
              signal: widget.signal,
              chart: chart,
              // 0 → 1 → 0: opacity 1 в начале и конце, .35 в середине
              pulse: 1 - 0.65 * (1 - (2 * pulse.value - 1).abs()),
            ),
            size: Size.infinite,
          ),
        ),
      ),
    );
  }
}

/// Заглушка вместо графика, когда свечей нет. Показывать вместо неё
/// синтетическую картинку нельзя: график обязан относиться к сделке.
class _ChartUnavailable extends StatelessWidget {
  const _ChartUnavailable();

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
                  Text('Живой график недоступен', style: T.body(13, weight: 700, color: C.muted)),
                  const SizedBox(height: 6),
                  Text(
                    'Для этой идеи не получены свечи от источника данных — '
                    'уровни сделки указаны в карточке ниже.',
                    textAlign: TextAlign.center,
                    style: T.body(11, color: C.dim, height: 1.5),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
}

class _ChartPainter extends CustomPainter {
  _ChartPainter({required this.signal, required this.chart, required this.pulse});

  final TradingSignal signal;
  final SignalChart chart;
  final double pulse;

  // Геометрия совпадает с макетом: поле графика слева, шкала цен справа.
  static const double _w = TradeChart.viewWidth;
  static const double _h = TradeChart.viewHeight;
  static const double _padTop = 12;
  static const double _padBottom = 16;
  static const double _plotLeft = 8;
  static const double _plotRight = 302;
  static const double _axisX = 308;

  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.scale(size.width / _w, size.height / _h);
    canvas.clipRect(const Rect.fromLTWH(0, 0, _w, _h));

    final candles = chart.candles;
    final tps = signal.takeProfits;

    // Диапазон цен: свечи плюс все уровни сделки, с небольшим запасом.
    var lo = signal.stopLoss;
    var hi = signal.entry;
    for (final tp in tps) {
      lo = math.min(lo, tp.price);
      hi = math.max(hi, tp.price);
    }
    for (final c in candles) {
      lo = math.min(lo, c.low);
      hi = math.max(hi, c.high);
    }
    final level = chart.breakLevel;
    if (level != null) {
      lo = math.min(lo, level);
      hi = math.max(hi, level);
    }
    final pad = math.max((hi - lo) * 0.05, 1e-9);
    lo -= pad;
    hi += pad;

    double y(double price) =>
        _padTop + (hi - price) / (hi - lo) * (_h - _padTop - _padBottom);

    final n = candles.length;
    final candleWidth = (_plotRight - _plotLeft) / n;
    double x(num i) => _plotLeft + i * candleWidth + candleWidth / 2;

    String label(double price) => fmt(price, signal.priceDecimals);

    // Фон.
    canvas.drawRect(const Rect.fromLTWH(0, 0, _w, _h), Paint()..color = C.inset);

    // Сетка и подписи шкалы.
    final gridPaint = Paint()
      ..color = C.grid
      ..strokeWidth = 1;
    for (var g = 0; g < 5; g++) {
      final price = lo + (hi - lo) * (g + .5) / 5;
      final gy = y(price);
      canvas.drawLine(Offset(6, gy), Offset(_plotRight, gy), gridPaint);
      _text(canvas, label(price), Offset(_axisX, gy + 3), size: 8.5, color: C.axis, mono: true);
    }

    // Зоны FVG — только реально найденные скринером.
    for (final zone in chart.zones) {
      final startX = x(zone.startIndex.clamp(0, n - 1)) - candleWidth / 2;
      final rect = Rect.fromLTRB(startX, y(math.max(zone.from, zone.to)), _plotRight,
          y(math.min(zone.from, zone.to)));
      canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(2)),
        Paint()..color = const Color.fromRGBO(255, 212, 0, .07),
      );
      _dashed(
        canvas,
        Path()..addRRect(RRect.fromRectAndRadius(rect, const Radius.circular(2))),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1
          ..color = const Color.fromRGBO(255, 212, 0, .35),
        dash: 3,
        gap: 3,
      );
      _text(canvas, zone.label, Offset(startX + 4, rect.top + 9),
          size: 8, color: C.accent, weight: 700);
    }

    // Слом структуры — если он был.
    if (level != null && chart.breakLabel != null) {
      _dashedLine(
        canvas,
        Offset(_plotLeft, y(level)),
        Offset(_plotRight, y(level)),
        Paint()
          ..color = C.muted
          ..strokeWidth = 1,
        dash: 2,
        gap: 3,
      );
      _text(
        canvas,
        '${chart.breakLabel} ${signal.direction.isLong ? '↑' : '↓'}',
        Offset(_plotLeft + 4, y(level) - 4),
        size: 8.5,
        color: C.textSecondary,
        weight: 700,
      );
    }

    // Зоны риска и профита от последней свечи до края поля.
    final toolLeft = x(n - 1) + 6;
    final entryY = y(signal.entry);
    final slY = y(signal.stopLoss);
    canvas.drawRect(
      Rect.fromLTRB(toolLeft, math.min(entryY, slY), _plotRight, math.max(entryY, slY)),
      Paint()..color = const Color.fromRGBO(255, 92, 92, .10),
    );
    if (tps.isNotEmpty) {
      final lastTpY = y(tps.last.price);
      canvas.drawRect(
        Rect.fromLTRB(toolLeft, math.min(entryY, lastTpY), _plotRight, math.max(entryY, lastTpY)),
        Paint()..color = const Color.fromRGBO(47, 213, 117, .08),
      );
    }

    // Линии входа, стопа и тейков.
    _dashedLine(
      canvas,
      Offset(_plotLeft, entryY),
      Offset(_plotRight, entryY),
      Paint()
        ..color = C.accent
        ..strokeWidth = 1.2,
      dash: 5,
      gap: 3,
    );
    _dashedLine(
      canvas,
      Offset(toolLeft - 30, slY),
      Offset(_plotRight, slY),
      Paint()
        ..color = C.red
        ..strokeWidth = 1,
      dash: 4,
      gap: 3,
    );
    for (var k = 0; k < tps.length; k++) {
      final ty = y(tps[k].price);
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
      _text(canvas, 'TP${k + 1}', Offset(toolLeft, ty - 3), size: 8, color: C.green, mono: true);
    }

    // Свечи — реальные данные, по которым считался сигнал.
    final bodyWidth = math.max(1.6, candleWidth * 0.52);
    for (var i = 0; i < n; i++) {
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
        Rect.fromLTWH(cx - bodyWidth / 2, top, bodyWidth, math.max(1, bottom - top)),
        paint,
      );
    }

    // Маркер текущей цены — пульсирует, как в макете.
    canvas.drawCircle(
      Offset(x(n - 1), y(candles.last.close)),
      3.4,
      Paint()..color = C.accent.withValues(alpha: pulse),
    );

    // Ценовые чипы на шкале: стоп, тейки, вход.
    void chip(double price, Color background, Color foreground) {
      canvas.drawRRect(
        RRect.fromRectAndRadius(
          Rect.fromLTWH(_axisX - 6, y(price) - 7.5, 80, 15),
          const Radius.circular(3),
        ),
        Paint()..color = background,
      );
      _text(canvas, label(price), Offset(_axisX, y(price) + 3.5),
          size: 9, color: foreground, weight: 600, mono: true);
    }

    chip(signal.stopLoss, C.chipSl, const Color(0xFFFFFFFF));
    for (final tp in tps) {
      chip(tp.price, C.chipTp, C.chipTpText);
    }
    chip(signal.entry, C.accent, C.onAccent);

    // Тег таймфрейма — всегда виден: зритель должен знать, что за свечи.
    _tag(canvas, chart.timeframeLabel, const Offset(_plotLeft, 8));

    canvas.restore();
  }

  /// Чип таймфрейма в левом верхнем углу.
  void _tag(Canvas canvas, String text, Offset topLeft) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: const TextStyle(
          fontFamily: 'JetBrains Mono',
          fontSize: 9,
          fontWeight: FontWeight.w700,
          fontVariations: [FontVariation('wght', 700)],
          color: C.accent,
          height: 1,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final rect = Rect.fromLTWH(topLeft.dx, topLeft.dy, painter.width + 12, 15);
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(3)),
      Paint()..color = const Color.fromRGBO(255, 212, 0, .10),
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(3)),
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = const Color.fromRGBO(255, 212, 0, .35),
    );
    painter.paint(canvas, Offset(rect.left + 6, rect.top + (rect.height - painter.height) / 2));
  }

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
