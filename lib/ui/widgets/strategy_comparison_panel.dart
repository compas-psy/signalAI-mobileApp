import 'package:flutter/material.dart';

import '../../data/api/experiment_comparison_client.dart';
import '../../domain/models/experiment_comparison.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Adds the latest persisted control/candidate evidence above the existing
/// Strategies screen. Loading begins only when this route is actually built.
class StrategyComparisonPanel extends StatefulWidget {
  const StrategyComparisonPanel({
    super.key,
    required this.child,
    this.loader,
  });

  final Widget child;
  final Future<ExperimentComparison> Function()? loader;

  @override
  State<StrategyComparisonPanel> createState() => _StrategyComparisonPanelState();
}

class _StrategyComparisonPanelState extends State<StrategyComparisonPanel> {
  ExperimentComparison? _comparison;
  Object? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final loader = widget.loader ?? ExperimentComparisonClient().latest;
      final value = await loader();
      if (!mounted) return;
      setState(() {
        _comparison = value;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 4),
            child: _loading
                ? const _ComparisonLoadingCard()
                : _error != null
                    ? const _ComparisonUnavailableCard()
                    : StrategyComparisonCard(comparison: _comparison!),
          ),
          Expanded(child: widget.child),
        ],
      );
}

class _ComparisonLoadingCard extends StatelessWidget {
  const _ComparisonLoadingCard();

  @override
  Widget build(BuildContext context) => const SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionLabel('Контроль vs кандидат'),
            SizedBox(height: 10),
            BusyBar(),
            SizedBox(height: 8),
            Text('Загружаю последнее сохранённое сравнение…'),
          ],
        ),
      );
}

class _ComparisonUnavailableCard extends StatelessWidget {
  const _ComparisonUnavailableCard();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Контроль vs кандидат'),
            const SizedBox(height: 7),
            Text(
              'Сравнение сейчас недоступно. Торговый движок продолжает '
              'работать независимо от этого экрана.',
              style: T.body(11.5, color: C.warning, height: 1.45),
            ),
          ],
        ),
      );
}

/// Read-only view of persisted experiment evidence.
class StrategyComparisonCard extends StatelessWidget {
  const StrategyComparisonCard({
    super.key,
    required this.comparison,
  });

  final ExperimentComparison comparison;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: switch (comparison.status) {
          ExperimentComparisonStatus.noExperiments => _noExperiments(),
          ExperimentComparisonStatus.pending => _pending(),
          ExperimentComparisonStatus.ready => _ready(),
        },
      );

  Widget _header({Widget? trailing}) => Row(
        children: [
          const Expanded(child: SectionLabel('Контроль vs кандидат')),
          ?trailing,
        ],
      );

  Widget _noExperiments() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _header(),
          const SizedBox(height: 7),
          Text(
            'Экспериментов пока нет. Текущая production-стратегия остаётся '
            'контролем; этот блок только показывает измерения.',
            style: T.body(11.5, color: C.muted, height: 1.45),
          ),
        ],
      );

  Widget _pending() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _header(
            trailing: OutlineBadge(
              label: 'данных недостаточно',
              color: C.warning,
              borderColor: C.warningBorder,
              background: C.warningFaint,
              fontWeight: 700,
            ),
          ),
          const SizedBox(height: 9),
          _identity(),
          const SizedBox(height: 8),
          Text(
            'Выборка: ${comparison.sampleSize ?? 0}. Пока нельзя делать '
            'вывод о преимуществе кандидата.',
            style: T.body(11.5, color: C.muted, height: 1.45),
          ),
        ],
      );

  Widget _ready() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _header(
            trailing: OutlineBadge(
              label: '${comparison.stage ?? '—'} · n=${comparison.sampleSize ?? 0}',
              color: comparison.sampleAdequate == true ? C.green : C.warning,
              borderColor: comparison.sampleAdequate == true
                  ? C.greenBorder
                  : C.warningBorder,
              background: comparison.sampleAdequate == true
                  ? C.greenFaint
                  : C.warningFaint,
              fontWeight: 700,
            ),
          ),
          const SizedBox(height: 9),
          _identity(),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _metric('Δ expectancy', _r(comparison.netExpectancyDeltaR)),
              _metric('Δ drawdown', _r(comparison.maxDrawdownDeltaR)),
              _metric('Δ hit-rate', _pp(comparison.hitRateDelta)),
              _metric('Δ calibration', _pp(comparison.calibrationDelta)),
              _metric('Overlap', _percent(comparison.opportunityOverlap)),
            ],
          ),
          if (comparison.latestDecision != null) ...[
            const SizedBox(height: 10),
            Text(
              'Последнее решение: ${comparison.latestDecision}',
              style: T.mono(10.5, color: C.muted),
            ),
          ],
          const SizedBox(height: 8),
          Text(
            'Только измерение: этот экран не переключает стратегию и не '
            'влияет на production-сигналы.',
            style: T.body(10.5, color: C.faint, height: 1.4),
          ),
        ],
      );

  Widget _identity() => InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'CONTROL  ${comparison.controlVersion ?? '—'}',
              style: T.mono(10.5, color: C.textSecondary),
            ),
            const SizedBox(height: 4),
            Text(
              'CANDIDATE  ${comparison.candidateVersion ?? '—'}',
              style: T.mono(10.5, color: C.accent),
            ),
          ],
        ),
      );

  Widget _metric(String label, String value) => Container(
        width: 132,
        padding: const EdgeInsets.fromLTRB(10, 8, 10, 9),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: T.body(9.5, color: C.faint)),
            const SizedBox(height: 3),
            Text(value, style: T.mono(12, color: C.text)),
          ],
        ),
      );

  String _r(double? value) => value == null ? '—' : '${_signed(value, 2)} R';

  String _pp(double? value) =>
      value == null ? '—' : '${_signed(value * 100, 1)} п.п.';

  String _percent(double? value) =>
      value == null ? '—' : '${(value * 100).round()}%';

  String _signed(double value, int digits) {
    final sign = value > 0 ? '+' : '';
    return '$sign${value.toStringAsFixed(digits).replaceAll('.', ',')}';
  }
}
