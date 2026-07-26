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
                if (summary.gate != null) ...[
                  const SizedBox(height: 12),
                  _GateCard(gate: summary.gate!),
                ],
                if (summary.exchange.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  _ExchangeCard(rows: summary.exchange),
                ],
                const SizedBox(height: 12),
                Padding(
                  padding: const EdgeInsets.fromLTRB(2, 4, 2, 0),
                  child: SectionLabel('Бумажные · ${summary.positions.length}'),
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
                if (summary.rejections.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  _RejectionsCard(rows: summary.rejections),
                ],
              ],
            ),
          ),
        ],
      );
}

/// Что стоит на бирже прямо сейчас.
///
/// Отдельным блоком и с пометкой площадки: бумажная сделка и настоящая позиция
/// не должны выглядеть одинаково ни при каком беглом взгляде.
class _ExchangeCard extends StatelessWidget {
  const _ExchangeCard({required this.rows});

  final List<ExchangeRow> rows;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('На бирже'),
            const SizedBox(height: 6),
            for (var i = 0; i < rows.length; i++)
              Padding(
                padding: EdgeInsets.only(bottom: i < rows.length - 1 ? 10 : 0),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Text(rows[i].symbol, style: T.body(13, weight: 700)),
                              const SizedBox(width: 6),
                              OutlineBadge(
                                label: rows[i].position ? 'ПОЗИЦИЯ' : 'ЗАЯВКА',
                                color: rows[i].position ? C.accent : C.muted,
                                borderColor: rows[i].position ? C.accentBorder : C.border,
                              ),
                            ],
                          ),
                          const SizedBox(height: 2),
                          Text(
                            rows[i].detail,
                            style: T.mono(11, color: toneColor(rows[i].tone)),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 10),
                    Text(rows[i].venue, style: T.body(10.5, color: C.muted)),
                  ],
                ),
              ),
          ],
        ),
      );
}

/// Прогресс допуска к живым деньгам.
class _GateCard extends StatelessWidget {
  const _GateCard({required this.gate});

  final GateView gate;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Допуск к живым деньгам')),
                OutlineBadge(
                  label: gate.allowed ? 'ОТКРЫТ' : 'ЗАКРЫТ',
                  color: gate.allowed ? C.green : C.muted,
                  borderColor: gate.allowed ? C.green : C.border,
                  fontWeight: 700,
                ),
              ],
            ),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: Stack(
                children: [
                  Container(height: 6, color: C.inset),
                  FractionallySizedBox(
                    widthFactor: gate.progress.clamp(0, 1),
                    child: Container(
                      height: 6,
                      color: gate.allowed ? C.green : C.accent,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 6),
            Text(gate.reason, style: T.body(11, color: C.muted, height: 1.4)),
          ],
        ),
      );
}

/// Что не прошло фильтры — и чего это стоило.
///
/// Отбраковка без последствий выглядит как осторожность; отбраковка, после
/// которой цена ушла в нашу сторону, — как упущенная сделка. Видеть надо и то,
/// и другое, иначе фильтры невозможно оценить.
class _RejectionsCard extends StatelessWidget {
  const _RejectionsCard({required this.rows});

  final List<RejectionRow> rows;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Отбраковано'),
            const SizedBox(height: 4),
            Text(
              'Ход цены за сутки после отказа: сколько стоила осторожность.',
              style: T.body(10.5, color: C.muted, height: 1.4),
            ),
            const SizedBox(height: 8),
            for (var i = 0; i < rows.length; i++)
              Padding(
                padding: EdgeInsets.only(bottom: i < rows.length - 1 ? 8 : 0),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 74,
                      child: Text(rows[i].symbol, style: T.body(12, weight: 600)),
                    ),
                    Expanded(
                      child: Text(
                        rows[i].reason,
                        style: T.body(10.5, color: C.muted, height: 1.35),
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(
                      rows[i].moveLabel,
                      style: T.mono(
                        11.5,
                        color: rows[i].moveLabel == '—'
                            ? C.dim
                            : rows[i].movePositive
                                ? C.green
                                : C.red,
                      ),
                    ),
                  ],
                ),
              ),
          ],
        ),
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
