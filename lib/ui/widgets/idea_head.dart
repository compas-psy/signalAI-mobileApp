import 'package:flutter/widgets.dart';

import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/models/signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import 'common.dart';

/// Бейдж состояния идеи. Цвет — по смыслу: жёлтый значит «требует решения
/// сейчас», серый — «формируется», красный — «исполнять нельзя».
///
/// Живёт здесь, а не на экране списка, потому что состояние идеи показывают
/// оба экрана. Две таблицы цветов для одного состояния — это не дублирование
/// кода, а расхождение показаний: в списке идея жёлтая, в разборе зелёная.
class StateBadge extends StatelessWidget {
  const StateBadge({super.key, required this.state});

  final IdeaState state;

  static Color colorOf(IdeaState state) => switch (state) {
        IdeaState.triggered => C.accent,
        IdeaState.ready => C.info,
        IdeaState.active => C.green,
        IdeaState.watch => C.muted,
        IdeaState.closed => C.muted,
        IdeaState.skipped => C.muted,
        IdeaState.expired => C.warning,
        // Рынок ушёл без нас — предупреждение, а не поломка: сетап был
        // верен, войти было негде.
        IdeaState.missed => C.warning,
        IdeaState.invalidated => C.red,
      };

  @override
  Widget build(BuildContext context) {
    final color = colorOf(state);
    return OutlineBadge(
      label: state.label,
      color: color,
      borderColor: color.withValues(alpha: 0.35),
      background: color.withValues(alpha: 0.12),
      fontWeight: 800,
    );
  }
}

/// Бейдж готовности из серверной выдачи.
///
/// Он намеренно не выводится из lifecycle state: `wait_for_trigger` должен
/// оставаться формирующимся кандидатом, даже если сводка принесла статус
/// TRIGGERED.
class ReadinessBadge extends StatelessWidget {
  const ReadinessBadge({super.key, required this.readiness});

  final IdeaReadiness readiness;

  @override
  Widget build(BuildContext context) {
    final color = readiness.canAct ? C.accent : C.info;
    return OutlineBadge(
      label: readiness.canAct ? 'Можно действовать' : 'Формируется',
      color: color,
      borderColor: color.withValues(alpha: 0.35),
      background: color.withValues(alpha: 0.12),
      fontWeight: 800,
    );
  }
}

/// Чип состояния с точкой — первый элемент шапки разбора (прототип: чип
/// статуса в `idea-topline`).
class IdeaStateChip extends StatelessWidget {
  const IdeaStateChip({super.key, required this.state});

  final IdeaState state;

  @override
  Widget build(BuildContext context) {
    final color = StateBadge.colorOf(state);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
      decoration: BoxDecoration(
        color: C.chip,
        border: Border.all(color: color.withValues(alpha: 0.35)),
        borderRadius: BorderRadius.circular(R.pill),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 6),
          Text(state.label, style: T.body(10.5, weight: 800, color: color)),
        ],
      ),
    );
  }
}

/// Серый чип контекста: рынок, стратегия.
class ContextChip extends StatelessWidget {
  const ContextChip(this.label, {super.key});

  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(
          color: C.chip,
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(label, style: T.body(10.5, weight: 700, color: C.textSecondary)),
      );
}

/// Шапка разбора идеи (прототип, блок `detail-head`).
///
/// Слева — контекст (состояние, рынок, стратегия), тикер с направлением и
/// строка «на чём считано · сколько живёт · версия стратегии». Справа —
/// оценка крупно и последняя цена.
///
/// Раньше всё это жило в липком заголовке одной строкой: состояние идеи,
/// срок жизни сигнала и версия стратегии не показывались вовсе, хотя решение
/// принимается именно по ним — просроченный Triggered и свежий Watch
/// выглядели одинаково.
class IdeaDetailHead extends StatelessWidget {
  const IdeaDetailHead({
    super.key,
    required this.signal,
    required this.idea,
    required this.now,
  });

  final TradingSignal signal;

  /// Полная идея движка. null — разбор не приехал, показываем то, что есть.
  final Idea? idea;

  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final state = idea?.state;
    final chips = <Widget>[
      if (idea != null &&
          state != null &&
          !state.isTerminal &&
          state != IdeaState.active)
        ReadinessBadge(readiness: idea!.readiness)
      else if (state != null)
        IdeaStateChip(state: state),
      ContextChip(signal.market.label),
      if (idea != null) ContextChip(idea!.strategy.label),
    ];
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(spacing: 6, runSpacing: 6, children: chips),
              const SizedBox(height: 9),
              Row(
                children: [
                  Flexible(
                    child: Text(
                      signal.symbol,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: T.jost(26, letterSpacing: -0.5),
                    ),
                  ),
                  const SizedBox(width: 8),
                  DirectionBadge(
                    label: signal.direction.label,
                    color: directionColor(signal.direction),
                    background: directionBackground(signal.direction),
                  ),
                ],
              ),
              const SizedBox(height: 4),
              // Тикер отдельно от имени: «SiU6» знает не всякий, кто помнит,
              // что торгует доллар к рублю с исполнением в сентябре.
              Text(
                signal.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: T.body(11.5, color: C.textSecondary),
              ),
              const SizedBox(height: 3),
              Text(
                _subtitle(),
                style: T.body(11.5, color: C.muted, height: 1.4),
              ),
            ],
          ),
        ),
        const SizedBox(width: 12),
        Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              // Оценка берётся у идеи, если разбор приехал: у сигнала свой
              // балл старого скринера, и рядом в ленте стоит идейный. Два
              // разных числа под одним именем — расхождение показаний, а не
              // мелочь оформления.
              '${idea?.score.value ?? signal.score}/100',
              style: T.mono(23, weight: 600, color: C.accent),
            ),
            Text('оценка качества', style: T.body(10, color: C.faint)),
            // Котировки у идеи движка нет: он присылает уровни, а не цену
            // последней сделки. Пустое место честнее прочерка, который
            // читается как «цена не пришла, но должна была».
            if (signal.lastPrice.isNotEmpty) ...[
              const SizedBox(height: 7),
              Text(signal.lastPrice, style: T.mono(14, weight: 600)),
              if (signal.changeLabel.isNotEmpty)
                Text(
                  signal.changeLabel,
                  style: T.mono(11, color: signal.changeUp ? C.green : C.red),
                ),
            ],
          ],
        ),
      ],
    );
  }

  /// «1D / 4H / 1H · сигнал живёт 42 мин · стратегия v1.0.3».
  ///
  /// Каждая часть необязательна: у идеи без разбора нет ни таймфреймов, ни
  /// версии стратегии, и выдумывать их нечем.
  String _subtitle() {
    final parts = <String>[
      if (idea != null && idea!.timeframes.isNotEmpty)
        idea!.timeframes.join(' / ')
      else if (signal.horizonLabel.isNotEmpty)
        signal.horizonLabel,
      ?_born(),
      ?_life(),
      if (idea != null) 'стратегия ${idea!.strategyVersion}',
    ];
    return parts.join(' · ');
  }

  /// Когда идея посчитана.
  ///
  /// «Сигнал живёт 4 дн 6 ч» — это обратный отсчёт, а не точка отсчёта: по
  /// нему нельзя понять, что на графике произошло **после** появления идеи.
  /// А именно это и решает, отработал сетап или ещё нет.
  String? _born() {
    final at = idea?.createdAt;
    if (at == null) return null;
    final local = at.toLocal();
    return 'идея от ${_two(local.day)}.${_two(local.month)} '
        '${_two(local.hour)}:${_two(local.minute)}';
  }

  static String _two(int v) => v.toString().padLeft(2, '0');

  /// Сколько живёт сигнал. Срок берётся у идеи, а при её отсутствии — у
  /// сигнала: это одно и то же время, пришедшее двумя путями.
  String? _life() {
    final until = idea?.validUntil ?? signal.validUntil;
    if (until == null) return null;
    final left = until.difference(now);
    if (left.isNegative) return 'срок истёк';
    return 'сигнал живёт ${formatDuration(left)}';
  }
}

/// Длительность по-русски: «42 мин», «3 ч 12 мин», «2 дн 4 ч».
String formatDuration(Duration d) {
  if (d.inMinutes < 1) return 'меньше минуты';
  if (d.inHours < 1) return '${d.inMinutes} мин';
  if (d.inDays < 1) {
    final minutes = d.inMinutes % 60;
    return minutes == 0 ? '${d.inHours} ч' : '${d.inHours} ч $minutes мин';
  }
  final hours = d.inHours % 24;
  return hours == 0 ? '${d.inDays} дн' : '${d.inDays} дн $hours ч';
}
