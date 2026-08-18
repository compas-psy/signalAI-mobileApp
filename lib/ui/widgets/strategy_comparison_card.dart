import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

typedef StrategyComparisonLoader = Future<StrategyComparisonView?> Function();

class StrategyComparisonView {
  const StrategyComparisonView({
    required this.controlVersion,
    required this.candidateVersion,
    required this.stage,
    required this.sampleSize,
    required this.sampleAdequate,
    required this.netExpectancyDelta,
    required this.maxDrawdownDelta,
    required this.hitRateDelta,
    required this.calibrationErrorDelta,
    required this.opportunityOverlap,
    required this.latestDecision,
  });

  final String controlVersion;
  final String candidateVersion;
  final String stage;
  final int sampleSize;
  final bool sampleAdequate;
  final double? netExpectancyDelta;
  final double? maxDrawdownDelta;
  final double? hitRateDelta;
  final double? calibrationErrorDelta;
  final double? opportunityOverlap;
  final String? latestDecision;

  factory StrategyComparisonView.fromApi({
    required Map<String, dynamic> summary,
    required Map<String, dynamic> comparison,
  }) {
    final control = _map(summary['control']);
    final candidate = _map(summary['candidate']);
    final run = _map(comparison['latest_run']);
    final result = _map(run['result']);
    final incremental = _map(result['incremental_control_delta']);
    final decision = _map(comparison['latest_decision']);
    final metrics = (comparison['metrics'] as List<dynamic>? ?? const [])
        .whereType<Map<String, dynamic>>()
        .toList(growable: false);

    double? metricDelta(String name) {
      for (final metric in metrics) {
        if (metric['name'] == name) return _number(metric['delta']);
      }
      return null;
    }

    return StrategyComparisonView(
      controlVersion: '${control['version'] ?? '—'}',
      candidateVersion: '${candidate['version'] ?? '—'}',
      stage: '${summary['stage'] ?? _map(comparison['evidence'])['stage'] ?? '—'}',
      sampleSize: (run['sample_size'] as num?)?.toInt() ??
          (_map(summary['latest_run'])['sample_size'] as num?)?.toInt() ??
          0,
      sampleAdequate: run['sample_adequate'] as bool? ??
          _map(summary['latest_run'])['sample_adequate'] as bool? ??
          false,
      netExpectancyDelta: metricDelta('net_expectancy_r') ??
          _number(incremental['incremental_net_expectancy_r']),
      maxDrawdownDelta: metricDelta('max_drawdown_r'),
      hitRateDelta: metricDelta('hit_rate') ?? _number(incremental['hit_rate_delta']),
      calibrationErrorDelta: metricDelta('calibration_error') ??
          _number(incremental['calibration_error_delta']),
      opportunityOverlap: _number(incremental['opportunity_overlap']),
      latestDecision: decision['decision']?.toString(),
    );
  }
}

class StrategyComparisonCard extends StatefulWidget {
  const StrategyComparisonCard({super.key, this.load});

  final StrategyComparisonLoader? load;

  @override
  State<StrategyComparisonCard> createState() => _StrategyComparisonCardState();
}

class _StrategyComparisonCardState extends State<StrategyComparisonCard> {
  ApiClient? _api;
  StrategyComparisonView? _view;
  String? _error;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<StrategyComparisonView?> _loadFromApi() async {
    final api = _api ??= ApiClient();
    final experiments = await api.getList('/api/v1/experiments?limit=1');
    if (experiments.isEmpty) return null;
    final first = experiments.first;
    if (first is! Map<String, dynamic>) return null;
    final id = first['id']?.toString();
    if (id == null || id.isEmpty) return null;
    final comparison = await api.get('/api/v1/experiments/$id/comparison');
    return StrategyComparisonView.fromApi(summary: first, comparison: comparison);
  }

  Future<void> _refresh() async {
    try {
      final view = await (widget.load ?? _loadFromApi)();
      if (!mounted) return;
      setState(() {
        _view = view;
        _error = null;
        _loading = false;
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _error = '$error';
        _loading = false;
      });
    }
  }

  @override
  void dispose() {
    _api?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final view = _view;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Контроль vs кандидат'),
          const SizedBox(height: 6),
          Text(
            'Сравнение сохранённого paired evidence. Этот блок ничего не переключает в торговом контуре.',
            style: T.body(10.5, color: C.muted, height: 1.45),
          ),
          const SizedBox(height: 10),
          if (_loading)
            const BusyLine(label: 'Читаем результаты эксперимента…')
          else if (_error != null)
            Text(
              'Сравнение временно недоступно: $_error',
              style: T.body(10.5, color: C.warning, height: 1.45),
            )
          else if (view == null)
            Text(
              'Экспериментов пока нет. Контроль продолжает работать без изменений.',
              style: T.body(11, color: C.muted, height: 1.45),
            )
          else ...[
            _VersionRow(label: 'Контроль', value: view.controlVersion),
            _VersionRow(label: 'Кандидат', value: view.candidateVersion),
            _VersionRow(label: 'Стадия', value: view.stage),
            const SizedBox(height: 8),
            Text(
              view.sampleAdequate
                  ? '${view.sampleSize} наблюдений · выборка достаточна'
                  : '${view.sampleSize} наблюдений · Данных пока недостаточно',
              style: T.body(
                10.5,
                color: view.sampleAdequate ? C.green : C.warning,
                weight: 700,
              ),
            ),
            const SizedBox(height: 10),
            _MetricRow(
              label: 'Δ net expectancy',
              value: _formatR(view.netExpectancyDelta),
            ),
            _MetricRow(
              label: 'Δ max drawdown',
              value: _formatR(view.maxDrawdownDelta),
            ),
            _MetricRow(
              label: 'Δ hit rate',
              value: _formatPercent(view.hitRateDelta),
            ),
            _MetricRow(
              label: 'Δ calibration error',
              value: _formatPercent(view.calibrationErrorDelta),
            ),
            _MetricRow(
              label: 'Overlap возможностей',
              value: _formatUnsignedPercent(view.opportunityOverlap),
            ),
            const SizedBox(height: 8),
            Text(
              'Последнее решение: ${view.latestDecision ?? 'ещё не принято'}',
              style: T.body(10.5, color: C.muted, height: 1.4),
            ),
          ],
        ],
      ),
    );
  }
}

class _VersionRow extends StatelessWidget {
  const _VersionRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Row(
          children: [
            Expanded(child: Text(label, style: T.body(10.5, color: C.muted))),
            const SizedBox(width: 12),
            Flexible(child: Text(value, style: T.mono(10.5, color: C.text))),
          ],
        ),
      );
}

class _MetricRow extends StatelessWidget {
  const _MetricRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 3),
        child: Row(
          children: [
            Expanded(child: Text(label, style: T.body(10.5, color: C.muted))),
            const SizedBox(width: 12),
            Text(value, style: T.mono(11, color: C.text)),
          ],
        ),
      );
}

Map<String, dynamic> _map(Object? value) =>
    value is Map<String, dynamic> ? value : const <String, dynamic>{};

double? _number(Object? value) => value is num ? value.toDouble() : null;

String _formatR(double? value) {
  if (value == null) return '—';
  return '${_signed(value, digits: 3)} R';
}

String _formatPercent(double? value) {
  if (value == null) return '—';
  return '${_signed(value * 100, digits: 1)}%';
}

String _formatUnsignedPercent(double? value) {
  if (value == null) return '—';
  return '${(value * 100).round()}%';
}

String _signed(double value, {required int digits}) {
  final magnitude = value.abs().toStringAsFixed(digits).replaceAll('.', ',');
  if (value > 0) return '+$magnitude';
  if (value < 0) return '−$magnitude';
  return magnitude;
}
