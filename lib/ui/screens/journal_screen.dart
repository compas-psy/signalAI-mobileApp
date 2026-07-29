import 'package:flutter/widgets.dart';

import '../../domain/idea/journal_metrics.dart';
import '../../domain/idea/skip_record.dart';
import '../../domain/models/portfolio.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/execution_strip.dart';
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
      JournalPill.trades => _TradesWithExecutions(summary: summary),
      JournalPill.skips => const SkipsView(),
      JournalPill.metrics => const MetricsView(),
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

/// Показатели журнала (ТЗ §12.1).
class MetricsView extends StatelessWidget {
  const MetricsView({super.key});

  @override
  Widget build(BuildContext context) {
    final metrics = AppScope.of(context).journalMetrics;
    if (metrics == null) {
      return const _Empty(
        title: 'Статистики нет',
        note: 'В этом режиме журнал сделок не ведётся, считать нечего.',
      );
    }
    if (metrics.overall.trades == 0) {
      return const _Empty(
        title: 'Сделок ещё не было',
        note: 'Показатели появятся после первой закрытой сделки. Пустые '
            'нули здесь врали бы: «ноль» и «не было» — разные состояния.',
      );
    }

    final monotonic = metrics.calibrationMonotonic;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 18),
      children: [
        _SliceCard(slice: metrics.overall, title: 'Итого'),
        const SizedBox(height: 12),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Калибровка оценки'),
              const SizedBox(height: 8),
              Text(
                switch (monotonic) {
                  null => 'Сравнивать нечего: закрытые сделки попали меньше '
                      'чем в две корзины оценки.',
                  true => 'Высокие оценки не хуже низких — шкала работает на '
                      'этой выборке.',
                  false => 'Высокие оценки идут хуже низких. На этой выборке '
                      'шкала не работает, и доверять ей нельзя.',
                },
                style: T.body(11.5,
                    color: monotonic == false ? C.red : C.muted, height: 1.45),
              ),
              const SizedBox(height: 10),
              for (final bucket in metrics.byScoreBucket) ...[
                _BucketRow(slice: bucket),
                if (bucket != metrics.byScoreBucket.last)
                  const SizedBox(height: 7),
              ],
            ],
          ),
        ),
        if (metrics.byStrategy.length > 1) ...[
          const SizedBox(height: 12),
          const SectionLabel('По стратегиям'),
          const SizedBox(height: 8),
          for (final slice in metrics.byStrategy) ...[
            _SliceCard(slice: slice, title: slice.name),
            if (slice != metrics.byStrategy.last) const SizedBox(height: S.gap),
          ],
        ],
      ],
    );
  }
}

class _SliceCard extends StatelessWidget {
  const _SliceCard({required this.slice, required this.title});

  final MetricSlice slice;
  final String title;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: SectionLabel(title)),
                Text('${slice.trades} сделок',
                    style: T.mono(11, color: C.muted)),
              ],
            ),
            const SizedBox(height: 10),
            _MetricRow('Матожидание', _r(slice.expectancyR), accent: true),
            const SizedBox(height: 6),
            _MetricRow('Profit factor', _two(slice.profitFactor)),
            const SizedBox(height: 6),
            _MetricRow('Доля прибыльных', _percent(slice.winRate)),
            const SizedBox(height: 6),
            _MetricRow('Средняя прибыль', _r(slice.averageWinR)),
            const SizedBox(height: 6),
            _MetricRow('Средний убыток', _r(slice.averageLossR)),
            const SizedBox(height: 6),
            _MetricRow('Макс. просадка',
                '−${slice.maxDrawdownR.toStringAsFixed(1).replaceAll('.', ',')}R'),
            if (!slice.conclusive) ...[
              const SizedBox(height: 10),
              Text(
                'Выборки мало для выводов: ${slice.trades} из '
                '${MetricSlice.minimumForConclusions} сделок. Числа настоящие, '
                'но делать по ним ставку рано.',
                style: T.body(11, color: C.warning, height: 1.4),
              ),
            ],
          ],
        ),
      );

  static String _r(double? v) =>
      v == null ? '—' : '${v >= 0 ? '+' : '−'}${v.abs().toStringAsFixed(2).replaceAll('.', ',')}R';

  static String _two(double? v) =>
      v == null ? '—' : v.toStringAsFixed(2).replaceAll('.', ',');

  static String _percent(double? v) =>
      v == null ? '—' : '${(v * 100).round()}%';
}

class _MetricRow extends StatelessWidget {
  const _MetricRow(this.label, this.value, {this.accent = false});

  final String label;
  final String value;
  final bool accent;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Expanded(child: Text(label, style: T.body(12, color: C.muted))),
          Text(value,
              style: T.mono(accent ? 13 : 12,
                  color: accent ? C.accent : C.text)),
        ],
      );
}

class _BucketRow extends StatelessWidget {
  const _BucketRow({required this.slice});

  final MetricSlice slice;

  @override
  Widget build(BuildContext context) {
    final expectancy = slice.expectancyR;
    return Row(
      children: [
        SizedBox(
          width: 62,
          child: Text(slice.name, style: T.mono(11, color: C.muted)),
        ),
        Expanded(
          child: Text(
            slice.trades == 0 ? 'сделок не было' : '${slice.trades} сделок',
            style: T.body(11.5, color: slice.trades == 0 ? C.faint : C.text),
          ),
        ),
        Text(
          expectancy == null
              ? '—'
              : '${expectancy >= 0 ? '+' : '−'}'
                  '${expectancy.abs().toStringAsFixed(2).replaceAll('.', ',')}R',
          style: T.mono(11,
              color: expectancy == null
                  ? C.faint
                  : expectancy >= 0
                      ? C.green
                      : C.red),
        ),
      ],
    );
  }
}

/// Сделки с ходом незавершённых исполнений сверху (ТЗ §11.3).
///
/// Открытая позиция без защиты — то, что владелец обязан увидеть первым,
/// раньше кривой капитала и статистики.
class _TradesWithExecutions extends StatelessWidget {
  const _TradesWithExecutions({required this.summary});

  final TradesSummary summary;

  @override
  Widget build(BuildContext context) {
    final live = AppScope.of(context).liveExecutions;
    if (live.isEmpty) return TradesScreen(summary: summary);

    final now = DateTime.now();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 0),
          child: Column(
            children: [
              for (final execution in live) ...[
                ExecutionStrip(execution: execution, now: now),
                const SizedBox(height: S.gap),
              ],
            ],
          ),
        ),
        Expanded(child: TradesScreen(summary: summary)),
      ],
    );
  }
}
