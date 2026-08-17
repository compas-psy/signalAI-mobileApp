import 'idea.dart';
import 'idea_state.dart';
import 'paper_position.dart';

/// Presentation projection for the owner's trading lifecycle.
///
/// It does not make trading decisions. The engine still owns readiness,
/// lifecycle state and paper-trade status; this class only groups those facts
/// into the four questions the UI needs to answer.
class IdeaFunnelSnapshot {
  const IdeaFunnelSnapshot({
    required this.decisions,
    required this.forming,
    required this.pending,
    required this.open,
  });

  final List<Idea> decisions;
  final List<Idea> forming;
  final List<PaperPosition> pending;
  final List<PaperPosition> open;

  int get total => decisions.length + forming.length + pending.length + open.length;

  factory IdeaFunnelSnapshot.from({
    required List<Idea> ideas,
    required List<PaperPosition> trades,
    DateTime? now,
  }) {
    final at = now ?? DateTime.now();
    final liveTrades = [for (final trade in trades) if (trade.status.live) trade];
    final tradeIdeaIds = {for (final trade in liveTrades) trade.ideaId};
    final lockedSymbols = {
      for (final trade in liveTrades) trade.symbol.toUpperCase(),
    };

    final undecided = <Idea>[
      for (final idea in ideas)
        if (!idea.state.isTerminal &&
            idea.state != IdeaState.active &&
            !tradeIdeaIds.contains(idea.id) &&
            !lockedSymbols.contains(idea.symbolOrId.toUpperCase()))
          idea,
    ];
    final ranked = IdeaPriority.rank(undecided, at);
    final decisions = [
      for (final idea in ranked)
        if (idea.readiness.canAct && idea.actionable) idea,
    ];
    final decisionIds = {for (final idea in decisions) idea.id};

    return IdeaFunnelSnapshot(
      decisions: decisions,
      // Everything still undecided but not actionable is a candidate being
      // formed. The label intentionally describes what the owner should infer,
      // rather than exposing the engine's internal Watch/Ready vocabulary.
      forming: [for (final idea in ranked) if (!decisionIds.contains(idea.id)) idea],
      pending: [
        for (final trade in liveTrades)
          if (trade.status == PaperPositionStatus.pending) trade,
      ],
      open: [
        for (final trade in liveTrades)
          if (trade.status == PaperPositionStatus.open) trade,
      ],
    );
  }
}
