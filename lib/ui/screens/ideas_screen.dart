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
import 'engine_idea_screen.dart';

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

    // Идеи приходят с Engine API и не зависят от старого локального дайджеста.
    // Прежде экран ждал `controller.digest`, хотя серверная выдача уже была
    // загружена. В итоге исправный сервер выглядел как зависший локальный
    // скринер, а тап по карточке открывал объект из другого источника.
    final all = controller.ideas;
    final visible = filterIdeas(all, filter, now);
    if (visible.isEmpty) {
      return _Empty(
        filter: filter,
        total: all.length,
        unavailableReason: controller.ideasUnavailableReason,
        noSetupsReason: controller.noSetupsReason,
        onRetry: controller.refreshIdeas,
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
          // Открываем ту же серверную идею напрямую. `openSignal` относится к
          // старому TradingSignal и при несовпадении ID молча выбирал первый
          // локальный сигнал — опасная подмена торгового плана.
          onTap: () => openEngineIdea(context, visible[i]),
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
                              idea.instrumentName.isEmpty
                                  ? idea.instrumentId
                                  : idea.instrumentName,
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
                    ? 'ждём план'
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

/// Бейдж состояния идеи. Цвет — по смыслу: жёлтый значит «требует решения
/// сейчас», серый — «наблюдаем», красный — «исполнять нельзя».
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
        IdeaState.invalidated => C.red,
      };

  @override
  Widget build(BuildContext context) {
    final color = StateBadge.colorOf(state);
    return OutlineBadge(
      label: state.label,
      color: color,
      borderColor: color.withValues(alpha: 0.35),
      background: color.withValues(alpha: 0.12),
      fontWeight: 800,
    );
  }
}

class _Empty extends StatelessWidget {
  const _Empty({
    required this.filter,
    required this.total,
    required this.onRetry,
    this.unavailableReason,
    this.noSetupsReason,
  });

  final IdeasPill filter;
  final int total;
  final VoidCallback onRetry;

  /// Почему движок не ответил. Это **не** «сетапов нет» (§24): обрыв связи и
  /// спокойный рынок выглядят на экране одинаково, если разницу не назвать.
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
                'Это не «сегодня нет сетапов»: идеи считает сервер. Пока он '
                'недоступен, приложение не делает вывод о рынке и не '
                'подставляет локальные идеи.',
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
              const SizedBox(height: 16),
              ActionButton(label: 'Повторить', onTap: onRetry, primary: true),
            ],
          ),
        ),
      );
    }

    final (title, note) = switch (filter) {
      IdeasPill.decisions => (
          'Решений нет',
          total == 0
              ? 'Движок отработал, но ни одна идея не прошла допуск. Это '
                  'нормальный результат — сделка не создаётся ради заполнения экрана.'
              : 'Ни одна идея не дошла до состояния, требующего решения. '
                  'Остальные — на вкладках «Наблюдение» и «Все».'
        ),
      IdeasPill.watch => (
          'Наблюдать нечего',
          'Идей с контекстом, но без триггера, сейчас нет.'
        ),
      IdeasPill.active => (
          'Открытых paper-позиций нет',
          'Здесь появятся идеи после подключения серверного paper-исполнения.'
        ),
      IdeasPill.all => (
          'Идей нет',
          noSetupsReason ??
              'Движок не нашёл идей, проходящих формальные условия стратегии.'
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
