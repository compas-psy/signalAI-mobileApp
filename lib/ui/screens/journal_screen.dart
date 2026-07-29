import 'package:flutter/widgets.dart';

import '../../domain/idea/skip_record.dart';
import '../../domain/models/portfolio.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import 'trades_screen.dart';

/// Раздел «Журнал» (ТЗ §12).
///
/// Три разреза: что сделано, чего не сделано и что из этого следует. Пропуски
/// стоят рядом со сделками не для полноты — там живут самые дорогие ошибки, и
/// их не видно, пока журнал показывает только исполненное.
class JournalScreen extends StatelessWidget {
  const JournalScreen({super.key, required this.pill, required this.summary});

  final int pill;
  final TradesSummary summary;

  @override
  Widget build(BuildContext context) {
    final section =
        JournalPill.values[pill.clamp(0, JournalPill.values.length - 1)];
    return switch (section) {
      JournalPill.trades => TradesScreen(summary: summary),
      JournalPill.skips => const SkipsView(),
      JournalPill.metrics => TradesScreen(summary: summary),
    };
  }
}

/// Пропущенные идеи и разрез по причинам (ТЗ §12).
class SkipsView extends StatelessWidget {
  const SkipsView({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final records = controller.skips;

    if (!controller.skipJournalAvailable) {
      return const _Empty(
        title: 'Журнал решений не ведётся',
        note: 'В этом режиме приложение не пишет на диск, поэтому пропуски '
            'сохранять некуда.',
      );
    }
    if (records.isEmpty) {
      return const _Empty(
        title: 'Пропусков нет',
        note: 'Здесь появятся идеи, от которых вы отказались, с указанной '
            'причиной. Это вторая половина статистики: без неё видно только '
            'то, что сделано, и не видно, что упущено.',
      );
    }

    final breakdown = SkipBreakdown.of(records);
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 18),
      children: [
        _BreakdownCard(breakdown: breakdown),
        const SizedBox(height: 12),
        const SectionLabel('Отказы'),
        const SizedBox(height: 8),
        for (final record in records) ...[
          _SkipCard(record: record),
          if (record != records.last) const SizedBox(height: S.gap),
        ],
      ],
    );
  }
}

class _BreakdownCard extends StatelessWidget {
  const _BreakdownCard({required this.breakdown});

  final SkipBreakdown breakdown;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Почему пропускаю')),
                Text('${breakdown.total}', style: T.mono(12, color: C.accent)),
              ],
            ),
            const SizedBox(height: 10),
            for (final entry in breakdown.ranked) ...[
              Row(
                children: [
                  Expanded(
                    child: Text(entry.key.label, style: T.body(12)),
                  ),
                  Text(
                    '${entry.value} · ${(breakdown.share(entry.key) * 100).round()}%',
                    style: T.mono(11, color: C.muted),
                  ),
                ],
              ),
              const SizedBox(height: 5),
              ClipRRect(
                borderRadius: BorderRadius.circular(R.pill),
                child: SizedBox(
                  height: 5,
                  child: Stack(
                    children: [
                      const ColoredBox(color: C.inset, child: SizedBox.expand()),
                      FractionallySizedBox(
                        widthFactor: breakdown.share(entry.key),
                        child: const ColoredBox(
                            color: C.accent, child: SizedBox.expand()),
                      ),
                    ],
                  ),
                ),
              ),
              if (entry != breakdown.ranked.last) const SizedBox(height: 9),
            ],
          ],
        ),
      );
}

class _SkipCard extends StatelessWidget {
  const _SkipCard({required this.record});

  final SkipRecord record;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text('${record.instrumentId} · ${record.direction}',
                      style: T.body(13, weight: 800)),
                ),
                Text('${record.score}/100', style: T.mono(11, color: C.muted)),
              ],
            ),
            const SizedBox(height: 6),
            Text(record.reason.label,
                style: T.body(12, weight: 700, color: C.accent)),
            if (record.comment.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(record.comment,
                  style: T.body(11.5, color: C.muted, height: 1.4)),
            ],
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                TagChip(_moment(record.at)),
                if (record.strategy != null) TagChip(record.strategy!),
                if (record.rrToSecondTarget != null)
                  TagChip('R/R ${_one(record.rrToSecondTarget!)}'),
                if (record.riskPercent != null)
                  TagChip('риск ${_two(record.riskPercent!)}%'),
              ],
            ),
          ],
        ),
      );

  static String _one(double v) =>
      v.toStringAsFixed(1).replaceAll('.', ',');

  static String _two(double v) =>
      v.toStringAsFixed(2).replaceAll('.', ',');

  static String _moment(DateTime at) =>
      '${at.day.toString().padLeft(2, '0')}.${at.month.toString().padLeft(2, '0')} '
      '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';
}

class _Empty extends StatelessWidget {
  const _Empty({required this.title, required this.note});

  final String title;
  final String note;

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
                note,
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
      );
}
