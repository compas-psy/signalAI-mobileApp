import 'package:flutter/widgets.dart';

import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_funnel.dart';
import '../../domain/idea/paper_position.dart';
import '../../state/app_controller.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import 'ideas_screen.dart';

/// Операционная лента thin-клиента: один инструмент — один текущий объект.
/// До решения это Idea, после подтверждения — server-side PaperTrade.
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
    final filter = IdeaFunnelPill
        .values[pill.clamp(0, IdeaFunnelPill.values.length - 1)];
    final now = DateTime.now();
    final allIdeas = controller.ideas;
    final funnel = IdeaFunnelSnapshot.from(
      ideas: allIdeas,
      trades: controller.paperPositions,
      now: now,
    );

    if (controller.ideasUnavailableReason != null && funnel.total == 0) {
      return _Unavailable(message: controller.ideasUnavailableReason!);
    }

    return switch (filter) {
      IdeaFunnelPill.all => _AllFunnel(
          controller: controller,
          funnel: funnel,
          allIdeas: allIdeas,
          now: now,
          openIdea: (idea) => _openIdea(context, idea),
        ),
      IdeaFunnelPill.decisions => _IdeaSlice(
          title: 'Нужно решить',
          emptyTitle: 'Решений нет',
          emptyText: 'Ни одна идея сейчас не требует подтверждения. '
              'Кандидаты, которые ещё ждут триггер, остаются в «Формируются».',
          ideas: funnel.decisions,
          now: now,
          openIdea: (idea) => _openIdea(context, idea),
        ),
      IdeaFunnelPill.forming => _IdeaSlice(
          title: 'Формируются',
          note: 'Кандидаты уже есть, но вход ещё не разрешён: сервер ждёт триггер.',
          emptyTitle: 'Кандидатов нет',
          emptyText: 'Сейчас нет сетапов, которые сформировались достаточно '
              'для наблюдения, но ещё ждут триггер.',
          ideas: funnel.forming,
          now: now,
          openIdea: (idea) => _openIdea(context, idea),
        ),
      IdeaFunnelPill.pending => _TradeSlice(
          title: 'Ждут входа',
          note: 'Решение уже принято и заявка выставлена. Позиции ещё нет: '
              'сервер ждёт, когда цена дойдёт до входа.',
          emptyTitle: 'Заявок на вход нет',
          emptyText: 'Здесь появятся подтверждённые сделки, у которых заявка '
              'уже выставлена, но цена ещё не дошла до входа.',
          trades: funnel.pending,
          controller: controller,
          allIdeas: allIdeas,
        ),
      IdeaFunnelPill.open => _TradeSlice(
          title: 'Позиции открыты',
          note: 'Вход уже исполнен. Эти позиции сопровождает сервер до закрытия.',
          emptyTitle: 'Открытых позиций нет',
          emptyText: 'Исполненные сделки появятся здесь после фактического входа.',
          trades: funnel.open,
          controller: controller,
          allIdeas: allIdeas,
        ),
    };
  }

  void _openIdea(BuildContext context, Idea idea) {
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

class _AllFunnel extends StatelessWidget {
  const _AllFunnel({
    required this.controller,
    required this.funnel,
    required this.allIdeas,
    required this.now,
    required this.openIdea,
  });

  final AppController controller;
  final IdeaFunnelSnapshot funnel;
  final List<Idea> allIdeas;
  final DateTime now;
  final ValueChanged<Idea> openIdea;

  @override
  Widget build(BuildContext context) {
    if (funnel.total == 0) {
      return const _Empty(
        title: 'Живых объектов нет',
        text: 'История завершённых идей и сделок находится в «Журнале».',
      );
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        if (funnel.decisions.isNotEmpty) ...[
          _GroupHeader(title: 'Нужно решить · ${funnel.decisions.length}'),
          const SizedBox(height: 8),
          for (final idea in funnel.decisions) ...[
            IdeaCard(idea: idea, now: now, onTap: () => openIdea(idea)),
            const SizedBox(height: 10),
          ],
        ],
        if (funnel.forming.isNotEmpty) ...[
          _GroupHeader(
            title: 'Формируются · ${funnel.forming.length}',
            note: 'Сетап уже виден, сервер ждёт подтверждения триггера.',
          ),
          const SizedBox(height: 8),
          for (final idea in funnel.forming) ...[
            IdeaCard(idea: idea, now: now, onTap: () => openIdea(idea)),
            const SizedBox(height: 10),
          ],
        ],
        if (funnel.pending.isNotEmpty) ...[
          _GroupHeader(
            title: 'Ждут входа · ${funnel.pending.length}',
            note: 'Заявка уже выставлена, позиции ещё нет.',
          ),
          const SizedBox(height: 8),
          for (final trade in funnel.pending) ...[
            _tradeCard(controller, allIdeas, trade),
            const SizedBox(height: 10),
          ],
        ],
        if (funnel.open.isNotEmpty) ...[
          _GroupHeader(
            title: 'Позиции открыты · ${funnel.open.length}',
            note: 'Вход исполнен, позицию сопровождает сервер.',
          ),
          const SizedBox(height: 8),
          for (final trade in funnel.open) ...[
            _tradeCard(controller, allIdeas, trade),
            const SizedBox(height: 10),
          ],
        ],
      ],
    );
  }
}

class _IdeaSlice extends StatelessWidget {
  const _IdeaSlice({
    required this.title,
    required this.emptyTitle,
    required this.emptyText,
    required this.ideas,
    required this.now,
    required this.openIdea,
    this.note,
  });

  final String title;
  final String? note;
  final String emptyTitle;
  final String emptyText;
  final List<Idea> ideas;
  final DateTime now;
  final ValueChanged<Idea> openIdea;

  @override
  Widget build(BuildContext context) {
    if (ideas.isEmpty) return _Empty(title: emptyTitle, text: emptyText);
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _GroupHeader(title: '$title · ${ideas.length}', note: note),
        const SizedBox(height: 8),
        for (final idea in ideas) ...[
          IdeaCard(idea: idea, now: now, onTap: () => openIdea(idea)),
          const SizedBox(height: 10),
        ],
      ],
    );
  }
}

class _TradeSlice extends StatelessWidget {
  const _TradeSlice({
    required this.title,
    required this.note,
    required this.emptyTitle,
    required this.emptyText,
    required this.trades,
    required this.controller,
    required this.allIdeas,
  });

  final String title;
  final String note;
  final String emptyTitle;
  final String emptyText;
  final List<PaperPosition> trades;
  final AppController controller;
  final List<Idea> allIdeas;

  @override
  Widget build(BuildContext context) {
    if (trades.isEmpty) return _Empty(title: emptyTitle, text: emptyText);
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _GroupHeader(title: '$title · ${trades.length}', note: note),
        const SizedBox(height: 8),
        for (final trade in trades) ...[
          _tradeCard(controller, allIdeas, trade),
          const SizedBox(height: 10),
        ],
      ],
    );
  }
}

Widget _tradeCard(
  AppController controller,
  List<Idea> allIdeas,
  PaperPosition trade,
) =>
    PaperPositionCard(
      trade: trade,
      idea: allIdeas.where((i) => i.id == trade.ideaId).firstOrNull,
      onOpenIdea:
          trade.ideaId.isEmpty ? null : () => controller.openSignal(trade.ideaId),
    );

class _GroupHeader extends StatelessWidget {
  const _GroupHeader({required this.title, this.note});

  final String title;
  final String? note;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionLabel(title),
          if (note != null) ...[
            const SizedBox(height: 4),
            Text(note!, style: T.body(10.5, color: C.muted, height: 1.4)),
          ],
        ],
      );
}

class _Unavailable extends StatelessWidget {
  const _Unavailable({required this.message});

  final String message;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Text(
            message,
            textAlign: TextAlign.center,
            style: T.body(12, color: C.warning, height: 1.5),
          ),
        ),
      );
}

class _Empty extends StatelessWidget {
  const _Empty({required this.title, required this.text});

  final String title;
  final String text;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(title, style: T.jost(18)),
              const SizedBox(height: 8),
              Text(
                text,
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
      );
}
