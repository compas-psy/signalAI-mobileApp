import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/idea/evidence.dart';
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
///
/// Видимость слоёв задаётся снаружи (ТЗ §10.1): выключенный слой не рисуется
/// вовсе. Свечи выключить нельзя — без них разметка висит в пустоте.
class TradeChart extends StatefulWidget {
  const TradeChart({
    super.key,
    required this.signal,
    this.annotations = const [],
    this.visibleLayers = allLayers,
    this.highlight = const {},
    this.chart,
  });

  /// Свечи, загруженные с движка (§23, `market/{id}/bars`).
  ///
  /// null — рисуем те, что пришли вместе с сигналом. Раньше второго источника
  /// не было вовсе: идеи считает движок, а свечи умел строить только скринер
  /// на устройстве — и разбор идеи всегда показывал заглушку.
  final SignalChart? chart;

  /// Слои, которые этот график умеет рисовать.
  ///
  /// Предлагать переключатель слоя, который нечем нарисовать, нельзя: чип
  /// нажимается, а на графике не меняется ничего — и владелец справедливо
  /// решает, что приложение сломано.
  static const renderableLayers = {
    ChartLayer.candles,
    ChartLayer.trend,
    ChartLayer.wyckoff,
    ChartLayer.smc,
    ChartLayer.priceAction,
    ChartLayer.levels,
    ChartLayer.volume,
    ChartLayer.openInterest,
    ChartLayer.events,
  };

  /// Слои по умолчанию.
  static const allLayers = renderableLayers;

  final TradingSignal signal;

  /// Разметка §10.6, найденная детекторами движка.
  ///
  /// Раньше график рисовал собственные уровни и не знал об этом списке вовсе:
  /// `Idea.annotations` не участвовал в отрисовке ни разу. Из-за этого
  /// приложение показывало меньше, чем нашёл движок, — Вайкоффа, Price
  /// Action, объёма и открытого интереса на графике не было в принципе.
  final List<ChartAnnotation> annotations;

  /// Какие слои показывать. Свечи подразумеваются всегда.
  final Set<ChartLayer> visibleLayers;

  /// Подсвеченные объекты разметки: `entry`, `stop`, `tp1`, `zone0`, `break`.
  ///
  /// ТЗ §9.1: тап по доказательству в тезисе обязан показать ровно тот
  /// объект на графике, о котором идёт речь. Ключи — хвосты идентификаторов
  /// аннотаций, которые собирает `IdeaMapper`.
  final Set<String> highlight;

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
    // Свечи движка приоритетнее сигнальных: идею считал он, и рисовать под
    // его разметкой чужие бары нельзя.
    final chart = widget.chart ?? widget.signal.chart;
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
              annotations: widget.annotations,
              layers: widget.visibleLayers,
              highlight: widget.highlight,
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

/// Как рисуется тип разметки.
///
/// Геометрия задаётся типом, а не слоем: order block и диапазон Вайкоффа
/// живут в разных слоях, но оба прямоугольники, а Spring и поглощение — оба
/// метки на конкретной свече.
enum _Geometry { band, level, marker, strip, moment }

_Geometry _geometry(AnnotationType type) => switch (type) {
      AnnotationType.wyckoffRange ||
      AnnotationType.smcOrderBlock ||
      AnnotationType.smcFvg ||
      AnnotationType.smcLiquidityPool ||
      AnnotationType.channel ||
      AnnotationType.levelEntry =>
        _Geometry.band,
      AnnotationType.levelStop ||
      AnnotationType.levelTarget ||
      AnnotationType.levelInvalidation ||
      AnnotationType.trendline ||
      AnnotationType.smcBos ||
      AnnotationType.smcChoch =>
        _Geometry.level,
      AnnotationType.wyckoffSpring ||
      AnnotationType.wyckoffUtad ||
      AnnotationType.wyckoffSos ||
      AnnotationType.wyckoffSow ||
      AnnotationType.smcSweep ||
      AnnotationType.paEngulfing ||
      AnnotationType.paPinBar ||
      AnnotationType.paRejection ||
      AnnotationType.paFailedBreakout =>
        _Geometry.marker,
      AnnotationType.volumeClimax || AnnotationType.oiBuildup => _Geometry.strip,
      AnnotationType.eventMarker || AnnotationType.planCreated =>
        _Geometry.moment,
    };

class _ChartPainter extends CustomPainter {
  _ChartPainter({
    required this.signal,
    required this.chart,
    required this.annotations,
    required this.layers,
    required this.highlight,
    required this.pulse,
  });

  final TradingSignal signal;
  final SignalChart chart;
  final List<ChartAnnotation> annotations;

  /// Включённые слои. Выключенный слой не рисуется — ТЗ §10.1.
  final Set<ChartLayer> layers;

  /// Подсвеченные объекты разметки.
  final Set<String> highlight;

  /// Приглушать ли всё, что не подсвечено. Пока подсветки нет, приглушать
  /// нечего: график в обычном состоянии показывает всё одинаково.
  bool get _dimming => highlight.isNotEmpty;

  /// Прозрачность объекта разметки с ключом [key].
  double _alpha(String key) =>
      !_dimming || highlight.contains(key) ? 1.0 : 0.22;

  final double pulse;

  bool get _showLevels => layers.contains(ChartLayer.levels);
  bool get _showSmc => layers.contains(ChartLayer.smc);

  // Геометрия совпадает с макетом: поле графика слева, шкала цен справа.
  static const double _w = TradeChart.viewWidth;
  static const double _h = TradeChart.viewHeight;
  static const double _padTop = 12;
  static const double _padBottom = 16;
  static const double _plotLeft = 8;
  static const double _plotRight = 302;

  /// Правый край **свечей**. Уровни, зоны и сетка идут до `_plotRight`, а
  /// свечи заканчиваются раньше: пустое поле справа — это ближайшее будущее,
  /// куда тянутся вход, стоп и цели. Без него последняя свеча упиралась в
  /// ценовую шкалу, и линии плана некуда было продолжить.
  static const double _candlesRight =
      _plotLeft + (_plotRight - _plotLeft) * 0.86;
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
    final candleWidth = (_candlesRight - _plotLeft) / n;
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

    // Зоны FVG — только реально найденные скринером и только при включённом
    // слое SMC.
    final zones = _showSmc ? chart.zones : const <ChartZone>[];
    for (var z = 0; z < zones.length; z++) {
      final zone = zones[z];
      final a = _alpha('zone$z');
      final startX = x(zone.startIndex.clamp(0, n - 1)) - candleWidth / 2;
      final rect = Rect.fromLTRB(startX, y(math.max(zone.from, zone.to)), _plotRight,
          y(math.min(zone.from, zone.to)));
      canvas.drawRRect(
        RRect.fromRectAndRadius(rect, const Radius.circular(2)),
        Paint()..color = C.accent.withValues(alpha: .07 * a),
      );
      _dashed(
        canvas,
        Path()..addRRect(RRect.fromRectAndRadius(rect, const Radius.circular(2))),
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = _dimming && highlight.contains('zone$z') ? 1.8 : 1
          ..color = C.accent.withValues(alpha: .35 * a),
        dash: 3,
        gap: 3,
      );
      _text(canvas, zone.label, Offset(startX + 4, rect.top + 9),
          size: 8, color: C.accent.withValues(alpha: a), weight: 700);
    }

    // Слом структуры — если он был и если слой включён.
    if (_showSmc && level != null && chart.breakLabel != null) {
      _dashedLine(
        canvas,
        Offset(_plotLeft, y(level)),
        Offset(_plotRight, y(level)),
        Paint()
          ..color = C.muted.withValues(alpha: _alpha('break'))
          ..strokeWidth = _dimming && highlight.contains('break') ? 1.8 : 1,
        dash: 2,
        gap: 3,
      );
      _text(
        canvas,
        '${chart.breakLabel} ${signal.direction.isLong ? '↑' : '↓'}',
        Offset(_plotLeft + 4, y(level) - 4),
        size: 8.5,
        color: C.textSecondary.withValues(alpha: _alpha('break')),
        weight: 700,
      );
    }

    final toolLeft = x(n - 1) + 6;
    final entryY = y(signal.entry);
    final slY = y(signal.stopLoss);

    // Уровни сделки: зоны риска и профита, линии входа, стопа и тейков.
    if (_showLevels) {
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
      _dashedLine(
        canvas,
        Offset(_plotLeft, entryY),
        Offset(_plotRight, entryY),
        Paint()
          ..color = C.accent.withValues(alpha: _alpha('entry'))
          ..strokeWidth = _dimming && highlight.contains('entry') ? 2 : 1.2,
        dash: 5,
        gap: 3,
      );
      _dashedLine(
        canvas,
        Offset(toolLeft - 30, slY),
        Offset(_plotRight, slY),
        Paint()
          ..color = C.red.withValues(alpha: _alpha('stop'))
          ..strokeWidth = _dimming && highlight.contains('stop') ? 1.8 : 1,
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

    // ── Разметка детекторов §10.6 ───────────────────────────────────────
    //
    // Рисуется поверх свечей и под ценовыми чипами: метка должна быть видна
    // на цене, но не закрывать числа, по которым выставляется заявка.
    //
    // Порядок — по display_priority §10.7, от низкого к высокому, чтобы
    // важное легло сверху. Сервер отдаёт список уже отсортированным по
    // убыванию, поэтому идём с конца.
    _paintAnnotations(canvas, x: x, y: y, candleWidth: candleWidth, count: n);

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

    if (_showLevels) {
      chip(signal.stopLoss, C.chipSl, const Color(0xFFFFFFFF));
      for (final tp in tps) {
        chip(tp.price, C.chipTp, C.chipTpText);
      }
      chip(signal.entry, C.accent, C.onAccent);
    }

    // Тег таймфрейма — всегда виден: зритель должен знать, что за свечи.
    _tag(canvas, chart.timeframeLabel, const Offset(_plotLeft, 8));

    canvas.restore();
  }

  /// Чип таймфрейма в левом верхнем углу.
  /// Подпись вертикальной метки момента.
  ///
  /// Прижимается к краю, если метка у самого борта: подпись, уехавшая за
  /// границу графика, не читается вовсе, а именно она и объясняет линию.
  void _momentLabel(Canvas canvas, String text, double cx, Color color, double alpha) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(
          fontFamily: 'JetBrains Mono',
          fontSize: 8.5,
          fontWeight: FontWeight.w700,
          fontVariations: const [FontVariation('wght', 700)],
          color: color.withValues(alpha: alpha),
          height: 1,
        ),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    final width = painter.width + 8;
    var left = cx + 3;
    if (left + width > _plotRight) left = cx - 3 - width;
    if (left < _plotLeft) left = _plotLeft;
    final rect = Rect.fromLTWH(left, _padTop + 1, width, 13);
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(3)),
      Paint()..color = const Color(0xE60B0B0D),
    );
    canvas.drawRRect(
      RRect.fromRectAndRadius(rect, const Radius.circular(3)),
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1
        ..color = color.withValues(alpha: alpha * 0.5),
    );
    painter.paint(canvas, Offset(left + 4, _padTop + 3));
  }

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

  /// Индекс свечи, на которую приходится момент [at].
  ///
  /// null — у свечей нет времени либо момент вне окна графика. Тогда метка,
  /// привязанная ко времени, **не рисуется**: поставленная наугад, она
  /// выглядела бы как факт, которого не было.
  int? _indexAt(DateTime at) {
    final candles = chart.candles;
    if (candles.isEmpty || candles.first.openTime == null) return null;
    var best = -1;
    for (var i = 0; i < candles.length; i++) {
      final t = candles[i].openTime;
      if (t == null) continue;
      if (!t.isAfter(at)) best = i;
    }
    return best < 0 ? null : best;
  }

  /// Цвет слоя. Разные слои обязаны отличаться на глаз: иначе разметка
  /// сливается в кашу и перестаёт что-либо объяснять.
  Color _layerColor(ChartLayer layer) => switch (layer) {
        ChartLayer.trend => C.textSecondary,
        ChartLayer.wyckoff => const Color(0xFFB07CFF),
        ChartLayer.smc => C.accent,
        ChartLayer.priceAction => const Color(0xFFFFB020),
        ChartLayer.levels => C.muted,
        ChartLayer.volume => const Color(0xFF6FA8FF),
        ChartLayer.openInterest => const Color(0xFF44C7B0),
        ChartLayer.events => C.warning,
        ChartLayer.candles => C.muted,
      };

  void _paintAnnotations(
    Canvas canvas, {
    required double Function(num) x,
    required double Function(double) y,
    required double candleWidth,
    required int count,
  }) {
    if (annotations.isEmpty) return;

    // От низкого приоритета к высокому: важное ложится сверху (§10.7).
    final ordered = [...annotations]
      ..sort((a, b) => a.displayPriority.compareTo(b.displayPriority));

    for (final a in ordered) {
      if (!layers.contains(a.layer)) continue;
      final alpha = _alpha(a.id) * (0.45 + 0.55 * a.confidence.clamp(0.0, 1.0));
      final color = _layerColor(a.layer).withValues(alpha: alpha);

      final startIndex = _indexAt(a.startTime);
      final endIndex = _indexAt(a.endTime);
      final low = a.priceLow;
      final high = a.priceHigh;

      switch (_geometry(a.type)) {
        case _Geometry.band:
          if (low == null || high == null) break;
          // Полоса без времени тянется на всю ширину: это уровень плана, он
          // действует всё окно, а не отрезок.
          final left = startIndex == null
              ? _plotLeft
              : x(startIndex) - candleWidth / 2;
          final right = endIndex == null
              ? _plotRight
              : math.max(left + 2, x(endIndex) + candleWidth / 2);
          final rect = Rect.fromLTRB(left, y(math.max(low, high)), right,
              y(math.min(low, high)));
          canvas.drawRRect(
            RRect.fromRectAndRadius(rect, const Radius.circular(2)),
            Paint()..color = color.withValues(alpha: alpha * 0.12),
          );
          canvas.drawRRect(
            RRect.fromRectAndRadius(rect, const Radius.circular(2)),
            Paint()
              ..style = PaintingStyle.stroke
              ..strokeWidth = 1
              ..color = color.withValues(alpha: alpha * 0.5),
          );
          if (a.label.isNotEmpty && rect.height > 9) {
            _text(canvas, a.label, Offset(left + 4, rect.top + 9),
                size: 8, color: color, weight: 700);
          }

        case _Geometry.level:
          final price = low ?? high;
          if (price == null) break;
          _dashedLine(
            canvas,
            Offset(_plotLeft, y(price)),
            Offset(_plotRight, y(price)),
            Paint()
              ..color = color
              ..strokeWidth = highlight.contains(a.id) ? 2 : 1,
            dash: 4,
            gap: 3,
          );
          if (a.label.isNotEmpty) {
            _text(canvas, a.label, Offset(_plotLeft + 4, y(price) - 4),
                size: 8, color: color, weight: 700);
          }

        case _Geometry.marker:
          if (startIndex == null) break;
          final price = high ?? low;
          if (price == null) break;
          final cx = x(startIndex);
          final cy = y(price) - 7;
          // Треугольник вершиной к свече: метка указывает на бар, а не
          // висит рядом с ним.
          final path = Path()
            ..moveTo(cx, cy + 5)
            ..lineTo(cx - 4, cy - 3)
            ..lineTo(cx + 4, cy - 3)
            ..close();
          canvas.drawPath(path, Paint()..color = color);
          if (a.label.isNotEmpty) {
            _text(canvas, a.label.split(':').first,
                Offset(cx + 6, cy), size: 7.5, color: color, weight: 700);
          }

        case _Geometry.strip:
          if (startIndex == null) break;
          // Подпанель внизу: объём и открытый интерес не делят шкалу с
          // ценой — совмещённые шкалы врут обе.
          final cx = x(startIndex);
          canvas.drawRect(
            Rect.fromLTWH(cx - candleWidth / 4, _h - _padBottom + 2,
                math.max(1.5, candleWidth / 2), 4),
            Paint()..color = color,
          );

        case _Geometry.moment:
          if (startIndex == null) break;
          final cx = x(startIndex);
          _dashedLine(
            canvas,
            Offset(cx, _padTop),
            Offset(cx, _h - _padBottom),
            Paint()
              ..color = color
              ..strokeWidth = 1,
            dash: 3,
            gap: 4,
          );
          // Момент без подписи — просто пунктир поперёк графика: видно, что
          // что-то произошло, и невозможно понять что. Именно на этом
          // спотыкался вопрос «на какой свече сформировалась идея».
          if (a.label.isNotEmpty) {
            _momentLabel(canvas, a.label, cx, color, alpha);
          }
      }
    }
  }

  @override
  bool shouldRepaint(_ChartPainter old) =>
      old.signal.id != signal.id ||
      old.pulse != pulse ||
      !setEquals(old.layers, layers) ||
      !setEquals(old.highlight, highlight);
}
