import 'package:flutter/widgets.dart';

import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'trade_chart.dart';

/// График идеи с панелью управления — единый блок, как в прототипе
/// (`chart-card`: тулбар сверху, разметка поверх графика, пояснение снизу).
///
/// Раньше график и переключатели слоёв были двумя отдельными элементами
/// ленты, между ними стоял отступ, и панель читалась как настройка экрана, а
/// не как управление этой картинкой.
class IdeaChartCard extends StatelessWidget {
  const IdeaChartCard({
    super.key,
    required this.signal,
    required this.idea,
    required this.available,
    required this.visible,
    required this.highlight,
    required this.onToggle,
    this.chart,
    this.timeframe = '',
    this.onTimeframe,
    this.loading = false,
    this.failed = false,
  });

  final TradingSignal signal;
  final Idea? idea;

  /// Выбранный таймфрейм. Пустая строка — берём тот, которым подписаны свечи.
  final String timeframe;

  /// Переключение таймфрейма. null — переключать нечем, и пилюли остаются
  /// подписью, а не кнопкой.
  final ValueChanged<String>? onTimeframe;

  /// Свечи запрошены и ещё не пришли.
  final bool loading;

  /// Источник не дал свечей этого таймфрейма.
  final bool failed;

  /// Свечи с движка. null — рисуем то, что пришло с сигналом.
  final SignalChart? chart;

  /// Слои, за которыми стоит доказательство этой идеи.
  final Set<ChartLayer> available;

  final Set<ChartLayer> visible;
  final Set<String> highlight;
  final ValueChanged<ChartLayer> onToggle;

  @override
  Widget build(BuildContext context) {
    final layers = [
      for (final layer in ChartLayer.values)
        if (available.contains(layer)) layer,
    ];
    final drawn = chart ?? signal.chart;
    final hasChart = drawn != null;
    // Активна та пилюля, чьи свечи **нарисованы**, а не та, что запрошена.
    //
    // Разница не теоретическая: карточка идеи открывается сводкой, у которой
    // таймфреймов ещё нет, и первый график грузится часовым. Потом приезжает
    // полная карточка, сетапный таймфрейм оказывается 4h — и тулбар начинал
    // подсвечивать «4h» поверх часовых свечей. График честно писал «1h» в
    // своём углу, и два элемента одного экрана спорили друг с другом.
    final active = drawn?.timeframeLabel ?? timeframe;
    // Тулбар живёт и без свечей: пока грузится другой таймфрейм,
    // переключатель обязан остаться на месте. Исчезающая панель управления
    // читается как поломка — владелец нажал кнопку, и кнопка пропала.
    final showToolbar = hasChart || loading || failed;
    return Container(
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: C.card,
        border: Border.all(color: C.border),
        borderRadius: BorderRadius.circular(R.card),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (showToolbar)
            _Toolbar(
              timeframes: _timeframes(active),
              active: active,
              onTimeframe: onTimeframe,
              layers: layers,
              visible: visible,
              onToggle: onToggle,
              loading: loading,
            ),
          Stack(
            children: [
              if (loading)
                _ChartPending('Загружаем свечи $active…')
              else if (failed && !hasChart)
                _ChartPending(
                  'Свечей $active источник не дал. Разметка и уровни ниже '
                  'считаны на ${_setupLabel()} и от таймфрейма картинки не '
                  'зависят.',
                )
              else
                TradeChart(
                  signal: signal,
                  chart: chart,
                  // Свеча, на которой движок принял решение (§10.7):
                  // без неё точка постановки идеи выглядит взятой с потолка.
                  bornAt: idea?.createdAt,
                  // Разметка §10.6 — та, что нашли детекторы движка.
                  annotations: idea?.annotations ?? const [],
                  visibleLayers: visible,
                  highlight: highlight,
                ),
              // Легенда справа: тег таймфрейма график рисует сам слева, и
              // накладывать на него ещё три пилюли значит спрятать оба.
              Positioned(
                top: 9,
                right: 9,
                child: Wrap(
                  alignment: WrapAlignment.end,
                  spacing: 5,
                  runSpacing: 5,
                  // Оценка и R:R — те же числа, что в шапке и в плане: у
                  // сигнала и у идеи они свои, и показывать здесь сигнальные
                  // значило бы подписать график чужими цифрами.
                  children: [
                    _LegendPill('Оценка ${idea?.score.value ?? signal.score}'),
                    ?_rrPill(),
                  ],
                ),
              ),
            ],
          ),
          if (hasChart && layers.length > 1)
            Container(
              padding: const EdgeInsets.fromLTRB(12, 9, 12, 11),
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: C.divider)),
              ),
              child: Text(
                'Верхний ряд — таймфрейм картинки, нижний — слои разметки. '
                'Разметка показывает не «всё подряд», а только факторы, '
                'вошедшие в оценку этой идеи; слой выключается, чтобы '
                'проверить каждую гипотезу отдельно. '
                '${_lockedNote(layers)}',
                style: T.body(10.5, color: C.faint, height: 1.45),
              ),
            ),
        ],
      ),
    );
  }

  /// Пилюля R:R. Значение плана точнее сигнального: план и есть то, что
  /// подписывают. Нет ни того, ни другого — пилюли нет.
  Widget? _rrPill() {
    final plan = idea?.plan;
    if (plan != null) {
      return _LegendPill(
        'R:R ${plan.rrToSecondTarget.toStringAsFixed(1).replaceAll('.', ',')}',
      );
    }
    return signal.riskReward.isEmpty ? null : _LegendPill('R:R ${signal.riskReward}');
  }

  /// Таймфреймы для переключателя — все, что участвовали в анализе.
  ///
  /// Раньше их показывали как подпись: свечи приезжали одним рядом, и
  /// нажимать было не на что. Теперь каждый таймфрейм — отдельный запрос
  /// к источнику, и кнопка делает ровно то, на что похожа.
  List<String> _timeframes(String chartTf) {
    final all = [...?idea?.timeframes];
    if (chartTf.isNotEmpty && !all.contains(chartTf)) all.insert(0, chartTf);
    return all;
  }

  /// Почему часть слоёв не нажимается.
  ///
  /// «Candles» выключить нельзя — без свечей разметка висит в пустоте, — но
  /// нажатие по нему выглядит как сломанная кнопка ровно до тех пор, пока
  /// причина не написана рядом.
  static String _lockedNote(List<ChartLayer> layers) {
    final locked = [for (final l in layers) if (l.alwaysOn) l.label];
    if (locked.isEmpty) return '';
    return locked.length == 1
        ? '«${locked.first}» не выключается: без свечей разметке не на чем стоять.'
        : '${locked.map((l) => '«$l»').join(', ')} не выключаются.';
  }

  /// Таймфрейм сетапа — на нём построена идея и лежит её разметка.
  String _setupLabel() {
    final list = idea?.timeframes ?? const <String>[];
    if (list.isEmpty) return 'сетапном таймфрейме';
    return list.length >= 2 ? list[1] : list.first;
  }
}

/// Место графика, пока свечей нет. Не заглушка «недоступно»: причина здесь
/// известна и названа, а высота сохраняется, чтобы лента не прыгала.
class _ChartPending extends StatelessWidget {
  const _ChartPending(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => AspectRatio(
        aspectRatio: TradeChart.viewWidth / TradeChart.viewHeight,
        child: ColoredBox(
          color: C.inset,
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Text(
                message,
                textAlign: TextAlign.center,
                style: T.body(11.5, color: C.dim, height: 1.5),
              ),
            ),
          ),
        ),
      );
}

class _Toolbar extends StatelessWidget {
  const _Toolbar({
    required this.timeframes,
    required this.active,
    required this.layers,
    required this.visible,
    required this.onToggle,
    this.onTimeframe,
    this.loading = false,
  });

  final List<String> timeframes;
  final String active;
  final ValueChanged<String>? onTimeframe;
  final bool loading;
  final List<ChartLayer> layers;
  final Set<ChartLayer> visible;
  final ValueChanged<ChartLayer> onToggle;

  @override
  Widget build(BuildContext context) {
    final switcher = onTimeframe;
    final tfGroup = timeframes.length < 2
        ? null
        : Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              for (final tf in timeframes) ...[
                if (tf != timeframes.first) const SizedBox(width: 4),
                _TimeframePill(
                  label: tf,
                  active: tf == active,
                  // Пока грузится один таймфрейм, второй не запрашиваем:
                  // два ответа на один график приходят вразнобой, и
                  // нарисован будет тот, что приехал последним, а не тот,
                  // что нажали.
                  onTap: switcher == null || loading || tf == active
                      ? null
                      : () => switcher(tf),
                ),
              ],
            ],
          );
    final layerGroup = layers.length < 2
        ? null
        : Wrap(
            spacing: 5,
            runSpacing: 5,
            children: [
              for (final layer in layers)
                _LayerPill(
                  layer: layer,
                  on: visible.contains(layer),
                  onTap: layer.alwaysOn ? null : () => onToggle(layer),
                ),
            ],
          );
    if (tfGroup == null && layerGroup == null) return const SizedBox.shrink();
    return Container(
      padding: const EdgeInsets.fromLTRB(11, 10, 11, 10),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: C.divider)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (tfGroup != null) ...[
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: tfGroup,
            ),
            if (layerGroup != null) const SizedBox(height: 8),
          ],
          ?layerGroup,
        ],
      ),
    );
  }
}

/// Таймфрейм графика. Активен ровно один — тот, чьи свечи нарисованы.
///
/// Остальные переключают картинку: разметка привязана ко времени, а не к
/// ряду баров, и на любом таймфрейме встаёт на свои места. Крупный показывает
/// контекст сделки, мелкий — как отработал триггер.
class _TimeframePill extends StatelessWidget {
  const _TimeframePill({
    required this.label,
    required this.active,
    this.onTap,
  });

  final String label;
  final bool active;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 6),
          decoration: BoxDecoration(
            color: active ? C.segmentActive : null,
            borderRadius: BorderRadius.circular(R.chipLg),
          ),
          child: Text(
            label,
            style: T.mono(
              11,
              weight: active ? 600 : 400,
              color: active ? C.text : C.dim,
            ),
          ),
        ),
      );
}

class _LayerPill extends StatelessWidget {
  const _LayerPill({required this.layer, required this.on, required this.onTap});

  final ChartLayer layer;
  final bool on;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    // Слой, который нельзя выключить, выглядит иначе нажимаемого: иначе
    // владелец жмёт по свечам и решает, что чип сломан.
    final locked = onTap == null;
    final color = locked
        ? C.dim
        : on
            ? C.accent
            : C.muted;
    return Pressable(
      onTap: onTap,
      // Без `alignment`: заданное выравнивание заставляет Container занять всю
      // доступную ширину, и внутри Wrap пилюли встают одна под другой во всю
      // строку вместо ряда чипов.
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: on && !locked ? C.accentFaint : C.inset,
          border: Border.all(color: on && !locked ? C.accentBorder : C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(layer.label, style: T.body(10.5, weight: 700, color: color)),
      ),
    );
  }
}

class _LegendPill extends StatelessWidget {
  const _LegendPill(this.label);

  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: const Color(0xCC0B0B0D),
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(label, style: T.body(10, weight: 700, color: C.textSecondary)),
      );
}
