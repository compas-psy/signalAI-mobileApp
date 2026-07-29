import 'package:flutter/widgets.dart';

import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/confluence_ring.dart';
import '../widgets/idea_head.dart';

/// Лента идей (ТЗ §8).
///
/// Карточка отвечает на пять вопросов и молчит обо всём остальном: что за
/// инструмент и куда, в каком состоянии, какой сетап и на каких таймфреймах,
/// сколько это стоит и сколько ещё живёт. Всё прочее — в разборе идеи.
class IdeasScreen extends StatelessWidget {
  const IdeasScreen({super.key, required this.pill});

  /// Разрез ленты из пилюли раздела.
  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final now = DateTime.now();
    final filter = IdeasPill.values[pill.clamp(0, IdeasPill.values.length - 1)];

    if (controller.digest == null) {
      return _Pending(
        loading: controller.digestLoading,
        stage: controller.analysisStage,
        error:
            controller.digestError == null ? null : controller.digestErrorText,
        onRetry: controller.refreshDigest,
      );
    }

    final all = controller.ideas;
    final visible = filterIdeas(all, filter, now);
    if (visible.isEmpty) {
      return _Empty(
        filter: filter,
        total: all.length,
        unavailableReason: controller.ideasUnavailableReason,
        noSetupsReason: controller.noSetupsReason,
      );
    }

    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 18),
      itemCount: visible.length,
      itemBuilder: (context, i) => Padding(
        padding: EdgeInsets.only(bottom: i == visible.length - 1 ? 0 : S.gap),
        child: IdeaCard(
          idea: visible[i],
          now: now,
          onTap: () => controller.openSignal(visible[i].id),
        ),
      ),
    );
  }

  /// Разрез по состоянию (ТЗ §8.2). Порядок внутри разреза — приоритет §6.2.
  static List<Idea> filterIdeas(
      List<Idea> ideas, IdeasPill filter, DateTime now) {
    final ranked = IdeaPriority.rank(ideas, now);
    return switch (filter) {
      IdeasPill.decisions =>
        ranked.where((i) => i.state.needsAttention).toList(),
      IdeasPill.watch =>
        ranked.where((i) => i.state == IdeaState.watch).toList(),
      IdeasPill.active =>
        ranked.where((i) => i.state == IdeaState.active).toList(),
      IdeasPill.all => ranked,
    };
  }
}

/// Карточка идеи в списке (ТЗ §8.1).
class IdeaCard extends StatelessWidget {
  const IdeaCard({
    super.key,
    required this.idea,
    required this.now,
    this.onTap,
  });

  final Idea idea;
  final DateTime now;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final plan = idea.plan;
    return Pressable(
      onTap: onTap,
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              idea.instrumentId,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: T.jost(17),
                            ),
                          ),
                          const SizedBox(width: 7),
                          DirectionBadge(
                            label: idea.direction.label,
                            color: directionColor(idea.direction),
                            background: directionBackground(idea.direction),
                          ),
                          const SizedBox(width: 6),
                          StateBadge(state: idea.state),
                        ],
                      ),
                      const SizedBox(height: 7),
                      Text(
                        '${idea.strategy.label} · ${idea.timeframes.join(" / ")}',
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(11.5, color: C.muted),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                ConfluenceRing(score: idea.score.value),
              ],
            ),
            const SizedBox(height: 11),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                TagChip(idea.market.label),
                TagChip(plan == null
                    ? 'плана нет'
                    : 'R/R ${_rr(plan.rrToSecondTarget)}'),
                TagChip(riskLabel(idea)),
                TagChip(ttlLabel(idea, now)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  static String _rr(double value) =>
      value.toStringAsFixed(1).replaceAll('.', ',');

  /// Риск сделки. Идея без права входа честно говорит «риск не выделен»,
  /// а не показывает ноль — ноль читается как «бесплатно».
  static String riskLabel(Idea idea) {
    final plan = idea.plan;
    if (plan == null || plan.riskRubles <= 0) return 'риск не выделен';
    return 'риск ${money(plan.riskRubles)} ₽ · '
        '${plan.riskPercent.toStringAsFixed(2).replaceAll('.', ',')}%';
  }

  static String ttlLabel(Idea idea, DateTime now) {
    if (idea.state.isTerminal) return idea.state.label;
    final left = idea.remaining(now);
    if (left.isNegative) return 'срок вышел';
    if (left.inHours >= 24) return 'ещё ${left.inDays} дн';
    if (left.inHours >= 1) {
      return 'ещё ${left.inHours} ч ${left.inMinutes % 60} мин';
    }
    return 'ещё ${left.inMinutes} мин';
  }

  static String money(double v) => v
      .round()
      .toString()
      .replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ' ');
}

class _Empty extends StatelessWidget {
  const _Empty({
    required this.filter,
    required this.total,
    this.unavailableReason,
    this.noSetupsReason,
  });

  final IdeasPill filter;
  final int total;

  /// Почему движок не ответил. Это **не** «сетапов нет» (§24): обрыв связи и
  /// спокойный рынок выглядят на экране одинаково, если разницу не назвать,
  /// и владелец спокойно ждёт сигналов от сервера, который лежит.
  final String? unavailableReason;

  /// Почему движок ответил пустым списком.
  final String? noSetupsReason;

  @override
  Widget build(BuildContext context) {
    if (unavailableReason != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Движок не ответил', style: T.jost(18)),
              const SizedBox(height: 8),
              Text(
                unavailableReason!,
                textAlign: TextAlign.center,
                style: T.body(12, color: C.warning, height: 1.5),
              ),
              const SizedBox(height: 8),
              Text(
                'Это не «сегодня нет сетапов»: идеи считает сервер, и пока он '
                'молчит, сказать про рынок нечего.',
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
      );
    }

    // Честный пустой экран (ТЗ §6): называет, чего именно нет и почему.
    final (title, note) = switch (filter) {
      IdeasPill.decisions => (
          'Решений нет',
          total == 0
              ? 'Расчёт не нашёл ни одной идеи выше порога показа. Это не '
                  'сбой: в узком рынке сетапов может не быть неделями.'
              : 'Ни одна идея не дошла до состояния, требующего решения. '
                  'Остальные — на вкладках «Наблюдение» и «Все».'
        ),
      IdeasPill.watch => (
          'Наблюдать нечего',
          'Идей с контекстом, но без триггера, сейчас нет.'
        ),
      IdeasPill.active => (
          'Открытых позиций нет',
          'Подтверждённые идеи появятся здесь вместе с заявками.'
        ),
      IdeasPill.all => (
          'Идей нет',
          noSetupsReason ??
              'Движок не нашёл ни одной идеи выше порога показа 65.'
        ),
    };
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(title, style: T.jost(18)),
            const SizedBox(height: 8),
            Text(
              note,
              textAlign: TextAlign.center,
              style: T.body(12, color: C.muted, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}

class _Pending extends StatelessWidget {
  const _Pending({
    required this.loading,
    required this.stage,
    required this.error,
    required this.onRetry,
  });

  final bool loading;
  final String? stage;
  final String? error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (loading || error == null) ...[
                BusyLine(label: stage ?? 'Подключаемся к биржам…'),
                const SizedBox(height: 12),
                Text(
                  'Идёт расчёт по котировкам MOEX ISS и Bybit. Остальные '
                  'разделы уже работают — расчёт не прервётся.',
                  textAlign: TextAlign.center,
                  style: T.body(11.5, color: C.muted, height: 1.5),
                ),
              ] else ...[
                Text('Данные бирж недоступны', style: T.jost(18)),
                const SizedBox(height: 8),
                Text(
                  error!,
                  textAlign: TextAlign.center,
                  style: T.body(12, color: C.muted, height: 1.5),
                ),
                const SizedBox(height: 16),
                ActionButton(label: 'Повторить', onTap: onRetry, primary: true),
              ],
            ],
          ),
        ),
      );
}
