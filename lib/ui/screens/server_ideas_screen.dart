import 'package:flutter/widgets.dart';

import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/idea/paper_position.dart';
import '../../state/app_controller.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import 'ideas_screen.dart';

/// Операционная лента thin-клиента.
///
/// Здесь сознательно нельзя показать одну сущность дважды. До решения
/// существует Idea; после подтверждения операционной сущностью становится
/// PaperTrade. Исходная идея остаётся доступна из сделки как объяснение, но
/// больше не занимает место в «Решениях», «Наблюдении» и «В работе».
class ServerIdeasScreen extends StatelessWidget {
  const ServerIdeasScreen({
    super.key,
    required this.controller,
    required this.pill,
  });

  final AppController controller;
  final int pill;

  @override
  Widget build(BuildContext context) {
    final filter = IdeasPill.values[pill.clamp(0, IdeasPill.values.length - 1)];
    final now = DateTime.now();
    final allIdeas = controller.ideas;
    final liveTrades = controller.paperPositions.where((t) => t.status.live).toList();
    final tradeIdeaIds = {for (final t in liveTrades) t.ideaId};

    // Идея, из которой уже создана серверная сделка, больше не является
    // самостоятельным операционным объектом. Убираем её по idea_id, а не по
    // тикеру: две разные исторические идеи одного инструмента должны
    // оставаться различимыми в «Все» после завершения.
    final undecided = [
      for (final idea in allIdeas)
        if (idea.state != IdeaState.active && !tradeIdeaIds.contains(idea.id)) idea,
    ];

    final ideas = switch (filter) {
      IdeasPill.decisions => IdeasScreen.filterIdeas(undecided, IdeasPill.decisions, now),
      IdeasPill.watch => IdeasScreen.filterIdeas(undecided, IdeasPill.watch, now),
      // «В работе» — только фактический execution lifecycle. Статус Active
      // у Idea больше не рисуется рядом со сделкой и не создаёт дубль PEPE.
      IdeasPill.active => const <Idea>[],
      // «Все» показывает живые предложения плюс сделки. Терминальная история
      // остаётся в серверном журнале, иначе эта вкладка снова превращается в
      // бесконечную свалку старых карточек.
      IdeasPill.all => undecided.where((i) => !i.state.isTerminal).toList(),
    };

    final trades = filter == IdeasPill.active || filter == IdeasPill.all
        ? liveTrades
        : const <PaperPosition>[];

    if (ideas.isEmpty && trades.isEmpty) {
      return _Empty(filter: filter, unavailable: controller.ideasUnavailableReason);
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        if (filter == IdeasPill.active) ...[
          _Meaning(
            title: 'В работе · ${trades.length}',
            text: 'Здесь только реальные server-side paper-сделки: заявка '
                'может ждать входа, позиция — сопровождаться, затем уйдёт в Журнал.',
          ),
          const SizedBox(height: 10),
        ],
        for (final idea in ideas) ...[
          IdeaCard(
            idea: idea,
            now: now,
            onTap: () => _openIdea(context, idea),
          ),
          const SizedBox(height: 10),
        ],
        if (trades.isNotEmpty && filter == IdeasPill.all) ...[
          const SizedBox(height: 2),
          const SectionLabel('Сделки в работе'),
          const SizedBox(height: 8),
        ],
        for (final trade in trades) ...[
          PaperPositionCard(
            trade: trade,
            idea: allIdeas.where((i) => i.id == trade.ideaId).firstOrNull,
            onOpenIdea: trade.ideaId.isEmpty
                ? null
                : () => controller.openSignal(trade.ideaId),
          ),
          const SizedBox(height: 10),
        ],
      ],
    );
  }

  void _openIdea(BuildContext context, Idea idea) {
    // Последняя страховка интерфейса от «догоняющей» сделки. Supervisor на
    // сервере является главным шлюзом, но между его 15-минутными тиками цена
    // может уже пройти дальнюю цель. Если загруженный график это показывает,
    // не открываем подтверждение старого плана и просим сервер освежить ленту.
    final plan = idea.plan;
    final chart = controller.ideaChart(idea.id);
    if (plan != null && chart != null && chart.candles.isNotEmpty) {
      final last = chart.candles.last.close;
      final far = plan.targets.isEmpty ? null : plan.targets.last.price;
      if (far != null) {
        final playedOut = idea.direction.isLong ? last >= far : last <= far;
        if (playedOut) {
          controller.showToast(
            'План уже дошёл до дальней цели. Подтверждать старый вход нельзя — обновляю состояние.',
            tone: ToastTone.warning,
          );
          controller.refreshIdeas();
          return;
        }
      }
    }
    controller.openSignal(idea.id);
  }
}

class _Meaning extends StatelessWidget {
  const _Meaning({required this.title, required this.text});
  final String title;
  final String text;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: T.body(13, weight: 800)),
            const SizedBox(height: 5),
            Text(text, style: T.body(10.5, color: C.muted, height: 1.45)),
          ],
        ),
      );
}

class _Empty extends StatelessWidget {
  const _Empty({required this.filter, this.unavailable});
  final IdeasPill filter;
  final String? unavailable;

  @override
  Widget build(BuildContext context) {
    if (unavailable != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Text(unavailable!,
              textAlign: TextAlign.center,
              style: T.body(12, color: C.warning, height: 1.5)),
        ),
      );
    }
    final (title, note) = switch (filter) {
      IdeasPill.decisions => (
          'Решений нет',
          'Здесь остаются только идеи, по которым ещё нужно ваше решение. '
              'После подтверждения карточка сразу переезжает в «В работе».',
        ),
      IdeasPill.watch => (
          'Наблюдать нечего',
          'Здесь только идеи, которые ждут подтверждения триггера и ещё не стали сделкой.',
        ),
      IdeasPill.active => (
          'Сделок в работе нет',
          'Подтверждённая paper-сделка появится здесь сразу, даже пока заявка только ждёт входа.',
        ),
      IdeasPill.all => (
          'Живых объектов нет',
          'История завершённых идей и сделок находится в «Журнале».',
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
            Text(note,
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5)),
          ],
        ),
      ),
    );
  }
}
