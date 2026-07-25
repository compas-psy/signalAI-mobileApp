import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/portfolio.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/sparkline.dart';

/// Экран «Сделки»: эквити, статистика, активные позиции и журнал (ТЗ §9).
class TradesScreen extends StatelessWidget {
  const TradesScreen({super.key, required this.summary});

  final TradesSummary summary;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const ScreenHeader(title: 'Сделки'),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 90),
              children: [
                _EquityCard(summary: summary),
                const SizedBox(height: 12),
                Padding(
                  padding: const EdgeInsets.fromLTRB(2, 4, 2, 0),
                  child: SectionLabel('Активные · ${summary.positions.length}'),
                ),
                for (final position in summary.positions) ...[
                  const SizedBox(height: 12),
                  _PositionCard(position: position),
                ],
                const SizedBox(height: 12),
                const Padding(
                  padding: EdgeInsets.fromLTRB(2, 4, 2, 0),
                  child: SectionLabel('Журнал · 7 дней'),
                ),
                const SizedBox(height: 12),
                SectionCard(
                  clip: true,
                  padding: EdgeInsets.zero,
                  child: Column(
                    children: [
                      for (final entry in summary.journal) _JournalRow(entry: entry),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      );
}

class _EquityCard extends StatelessWidget {
  const _EquityCard({required this.summary});

  final TradesSummary summary;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.fromLTRB(14, 13, 14, 13),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                Expanded(child: SectionLabel(summary.equityTitle)),
                Text(
                  summary.equityChange,
                  style: T.mono(15, weight: 600, color: C.green),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.only(top: 10, bottom: 4),
              child: Sparkline(values: summary.equityCurve, height: 64, color: C.green),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                for (final stat in summary.stats) ...[
                  Expanded(child: _StatBox(stat: stat)),
                  if (stat != summary.stats.last) const SizedBox(width: 8),
                ],
              ],
            ),
          ],
        ),
      );
}

class _StatBox extends StatelessWidget {
  const _StatBox({required this.stat});

  final StatTile stat;

  @override
  Widget build(BuildContext context) => InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              stat.value,
              maxLines: 1,
              style: T.mono(13, weight: 600, color: toneColor(stat.tone)),
            ),
            const SizedBox(height: 2),
            Text(stat.label, maxLines: 1, style: T.body(10, color: C.muted)),
          ],
        ),
      );
}

class _PositionCard extends StatelessWidget {
  const _PositionCard({required this.position});

  final ActivePosition position;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.fromLTRB(14, 13, 14, 13),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text(position.symbol, style: T.jost(15)),
                const SizedBox(width: 8),
                DirectionBadge(
                  label: position.direction.label,
                  color: directionColor(position.direction),
                  background: directionBackground(position.direction),
                ),
                const Spacer(),
                Text(
                  position.pnlLabel,
                  style: T.mono(13,
                      weight: 600, color: position.pnlPositive ? C.green : C.red),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text('вход ${position.entryLabel}', style: T.mono(11, color: C.muted)),
                Text('сейчас ${position.currentLabel}', style: T.mono(11)),
              ],
            ),
            const SizedBox(height: 7),
            // Прогресс до ближайшего тейка.
            ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: Container(
                height: 5,
                color: C.border,
                child: FractionallySizedBox(
                  alignment: Alignment.centerLeft,
                  widthFactor: (position.progressPercent / 100).clamp(0, 1),
                  child: const DecoratedBox(
                    decoration: BoxDecoration(
                      gradient: LinearGradient(colors: [C.accent, C.green]),
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 5),
            Text(position.stage, style: T.body(10, color: C.muted)),
          ],
        ),
      );
}

class _JournalRow extends StatelessWidget {
  const _JournalRow({required this.entry});

  final JournalEntry entry;

  @override
  Widget build(BuildContext context) {
    final resultColor = entry.rMultiple < 0 ? C.red : C.green;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      decoration: const BoxDecoration(
        border: Border(bottom: BorderSide(color: C.divider)),
      ),
      child: Row(
        children: [
          SizedBox(width: 42, child: Text(entry.date, style: T.mono(11, color: C.muted))),
          const SizedBox(width: 10),
          Expanded(child: Text(entry.symbol, style: T.body(12, weight: 700))),
          const SizedBox(width: 10),
          SizedBox(
            width: 14,
            child: Text(
              entry.directionLetter,
              style: T.mono(11, weight: 700, color: directionColor(entry.direction)),
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 36,
            child: Text(
              entry.outcome,
              textAlign: TextAlign.right,
              style: T.body(10, color: C.muted),
            ),
          ),
          const SizedBox(width: 10),
          SizedBox(
            width: 48,
            child: Text(
              rMultiple(entry.rMultiple),
              textAlign: TextAlign.right,
              style: T.mono(12, weight: 600, color: resultColor),
            ),
          ),
        ],
      ),
    );
  }
}
