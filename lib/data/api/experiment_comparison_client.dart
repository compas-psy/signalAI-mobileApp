import '../../domain/models/experiment_comparison.dart';
import 'api_client.dart';

/// Read-only client for SAI-013 experiment evidence.
///
/// It performs GETs only. Promotion and strategy-role mutation are
/// intentionally absent from this API surface.
class ExperimentComparisonClient {
  ExperimentComparisonClient([ApiClient? api]) : _api = api ?? ApiClient();

  final ApiClient _api;
  static const _base = '/api/v1';

  Future<ExperimentComparison> latest() async {
    final experiments = await _api.getList('$_base/experiments?limit=1');
    if (experiments.isEmpty) {
      return const ExperimentComparison.noExperiments();
    }

    final summary = _map(experiments.first, 'experiment summary');
    final id = '${summary['id'] ?? ''}'.trim();
    if (id.isEmpty) {
      throw ApiException('Сервер вернул эксперимент без id.');
    }

    final json = await _api.get('$_base/experiments/$id/comparison');
    return _parse(json, fallback: summary);
  }

  ExperimentComparison _parse(
    Map<String, dynamic> json, {
    required Map<String, dynamic> fallback,
  }) {
    final experiment = _map(json['experiment'], 'experiment');
    final evidence = _map(json['evidence'], 'evidence');
    final run = json['latest_run'] is Map<String, dynamic>
        ? json['latest_run'] as Map<String, dynamic>
        : null;

    final control = fallback['control'] is Map<String, dynamic>
        ? fallback['control'] as Map<String, dynamic>
        : const <String, dynamic>{};
    final candidate = fallback['candidate'] is Map<String, dynamic>
        ? fallback['candidate'] as Map<String, dynamic>
        : const <String, dynamic>{};

    final experimentId = '${experiment['id'] ?? fallback['id'] ?? ''}';
    final name = '${experiment['name'] ?? fallback['name'] ?? ''}';
    final controlVersion =
        '${experiment['control_version'] ?? control['version'] ?? ''}';
    final candidateVersion =
        '${experiment['candidate_version'] ?? candidate['version'] ?? ''}';
    final stage = '${evidence['stage'] ?? fallback['stage'] ?? ''}';
    final sampleSize = (run?['sample_size'] as num?)?.toInt();
    final sampleAdequate = run?['sample_adequate'] as bool?;

    if (run == null || sampleAdequate != true) {
      return ExperimentComparison.pending(
        experimentId: experimentId,
        name: name,
        controlVersion: controlVersion,
        candidateVersion: candidateVersion,
        stage: stage,
        sampleSize: sampleSize,
        sampleAdequate: sampleAdequate,
      );
    }

    final metrics = <String, double>{};
    final rawMetrics = json['metrics'];
    if (rawMetrics is List) {
      for (final raw in rawMetrics) {
        if (raw is! Map<String, dynamic>) continue;
        final name = raw['name'];
        final delta = raw['delta'];
        if (name is String && delta is num) metrics[name] = delta.toDouble();
      }
    }

    double? overlap;
    final result = run['result'];
    if (result is Map<String, dynamic>) {
      final incremental = result['incremental_control_delta'];
      if (incremental is Map<String, dynamic>) {
        overlap = (incremental['opportunity_overlap'] as num?)?.toDouble();
      }
    }

    final decision = json['latest_decision'];
    final latestDecision = decision is Map<String, dynamic>
        ? decision['decision'] as String?
        : null;

    return ExperimentComparison.ready(
      experimentId: experimentId,
      name: name,
      controlVersion: controlVersion,
      candidateVersion: candidateVersion,
      stage: stage,
      sampleSize: sampleSize ?? 0,
      sampleAdequate: true,
      netExpectancyDeltaR: metrics['net_expectancy_r'],
      maxDrawdownDeltaR: metrics['max_drawdown_r'],
      hitRateDelta: metrics['hit_rate'],
      calibrationDelta: metrics['calibration_error'],
      opportunityOverlap: overlap,
      latestDecision: latestDecision,
    );
  }

  Map<String, dynamic> _map(Object? value, String field) {
    if (value is Map<String, dynamic>) return value;
    throw ApiException('Сервер вернул некорректный блок $field.');
  }
}
