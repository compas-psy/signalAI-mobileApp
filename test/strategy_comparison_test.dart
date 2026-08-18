import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/domain/models/experiment_comparison.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/navigation.dart';
import 'package:signalai/ui/screens/strategies_screen.dart';

class _ExperimentApi extends ApiClient {
  _ExperimentApi({
    required this.experiments,
    this.comparison = const <String, dynamic>{},
  }) : super(baseUrl: 'https://engine.test', deviceToken: '');

  final List<dynamic> experiments;
  final Map<String, dynamic> comparison;
  final List<String> paths = [];

  @override
  Future<List<dynamic>> getList(String path) async {
    paths.add(path);
    return experiments;
  }

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    return comparison;
  }
}

class _ComparisonEngine extends EngineClient {
  _ComparisonEngine(this.value) : super(client: _ExperimentApi(experiments: const []));

  final ExperimentComparison value;
  int calls = 0;

  @override
  Future<ExperimentComparison> strategyComparison() async {
    calls += 1;
    return value;
  }
}

ExperimentComparison _readyComparison() => const ExperimentComparison.ready(
      experimentId: 'exp-1',
      name: 'trend-v2 vs legacy',
      controlVersion: 'legacy_control_v1',
      candidateVersion: 'candidate_trend_v2',
      stage: 'OOS',
      sampleSize: 120,
      sampleAdequate: true,
      netExpectancyDeltaR: 0.14,
      maxDrawdownDeltaR: -0.30,
      hitRateDelta: 0.08,
      calibrationDelta: -0.03,
      opportunityOverlap: 0.72,
      latestDecision: 'KEEP_CANDIDATE',
    );

void main() {
  group('EngineClient strategy comparison', () {
    test('empty experiment list is an explicit no-experiments state', () async {
      final api = _ExperimentApi(experiments: const []);

      final result = await EngineClient(client: api).strategyComparison();

      expect(result.status, ExperimentComparisonStatus.noExperiments);
      expect(api.paths, ['/api/v1/experiments?limit=1']);
    });

    test('latest experiment is parsed from persisted comparison evidence', () async {
      final api = _ExperimentApi(
        experiments: const [
          {
            'id': 'exp-1',
            'name': 'trend-v2 vs legacy',
            'control': {
              'family': 'TREND_PULLBACK',
              'version': 'legacy_control_v1',
            },
            'candidate': {
              'family': 'TREND_PULLBACK',
              'version': 'candidate_trend_v2',
            },
            'stage': 'OOS',
            'dataset_name': 'short_horizon_features',
            'latest_run': {
              'id': 'run-1',
              'evaluated_at': '2026-08-18T16:00:00+00:00',
              'sample_size': 120,
              'sample_adequate': true,
            },
          }
        ],
        comparison: const {
          'experiment': {
            'id': 'exp-1',
            'name': 'trend-v2 vs legacy',
            'control_family': 'TREND_PULLBACK',
            'control_version': 'legacy_control_v1',
            'candidate_family': 'TREND_PULLBACK',
            'candidate_version': 'candidate_trend_v2',
          },
          'evidence': {
            'stage': 'OOS',
          },
          'latest_run': {
            'id': 'run-1',
            'sample_size': 120,
            'sample_adequate': true,
            'result': {
              'incremental_control_delta': {
                'opportunity_overlap': 0.72,
              },
            },
          },
          'metrics': [
            {
              'name': 'net_expectancy_r',
              'control_value': 0.10,
              'candidate_value': 0.24,
              'delta': 0.14,
            },
            {
              'name': 'max_drawdown_r',
              'control_value': 0.70,
              'candidate_value': 0.40,
              'delta': -0.30,
            },
            {
              'name': 'hit_rate',
              'control_value': 0.50,
              'candidate_value': 0.58,
              'delta': 0.08,
            },
            {
              'name': 'calibration_error',
              'control_value': 0.12,
              'candidate_value': 0.09,
              'delta': -0.03,
            },
          ],
          'latest_decision': {
            'decision': 'KEEP_CANDIDATE',
            'source': 'OWNER',
            'actor': 'owner',
            'reason': 'continue to shadow',
          },
        },
      );

      final result = await EngineClient(client: api).strategyComparison();

      expect(result.status, ExperimentComparisonStatus.ready);
      expect(result.controlVersion, 'legacy_control_v1');
      expect(result.candidateVersion, 'candidate_trend_v2');
      expect(result.stage, 'OOS');
      expect(result.sampleSize, 120);
      expect(result.sampleAdequate, isTrue);
      expect(result.netExpectancyDeltaR, closeTo(0.14, 1e-9));
      expect(result.maxDrawdownDeltaR, closeTo(-0.30, 1e-9));
      expect(result.hitRateDelta, closeTo(0.08, 1e-9));
      expect(result.calibrationDelta, closeTo(-0.03, 1e-9));
      expect(result.opportunityOverlap, closeTo(0.72, 1e-9));
      expect(result.latestDecision, 'KEEP_CANDIDATE');
      expect(api.paths, [
        '/api/v1/experiments?limit=1',
        '/api/v1/experiments/exp-1/comparison',
      ]);
    });
  });

  group('StrategyComparisonCard', () {
    Future<void> pumpCard(WidgetTester tester, ExperimentComparison value) async {
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: StrategyComparisonCard(comparison: value),
            ),
          ),
        ),
      );
    }

    testWidgets('explains when no experiments exist yet', (tester) async {
      await pumpCard(tester, const ExperimentComparison.noExperiments());

      expect(find.text('КОНТРОЛЬ VS КАНДИДАТ'), findsOneWidget);
      expect(find.textContaining('Экспериментов пока нет'), findsOneWidget);
    });

    testWidgets('marks pending or inadequate evidence without pretending victory',
        (tester) async {
      await pumpCard(
        tester,
        const ExperimentComparison.pending(
          experimentId: 'exp-2',
          name: 'candidate pending',
          controlVersion: 'legacy_control_v1',
          candidateVersion: 'candidate_v2',
          stage: 'OOS',
          sampleSize: 18,
          sampleAdequate: false,
        ),
      );

      expect(find.textContaining('данных недостаточно'), findsOneWidget);
      expect(find.textContaining('18'), findsWidgets);
      expect(find.text('Продвинуть'), findsNothing);
      expect(find.text('Promote'), findsNothing);
    });

    testWidgets('shows paired deltas and remains read-only', (tester) async {
      await pumpCard(tester, _readyComparison());

      expect(find.textContaining('legacy_control_v1'), findsOneWidget);
      expect(find.textContaining('candidate_trend_v2'), findsOneWidget);
      expect(find.textContaining('+0,14 R'), findsOneWidget);
      expect(find.textContaining('-0,30 R'), findsOneWidget);
      expect(find.textContaining('+8,0 п.п.'), findsOneWidget);
      expect(find.textContaining('-3,0 п.п.'), findsOneWidget);
      expect(find.textContaining('72%'), findsOneWidget);
      expect(find.textContaining('KEEP_CANDIDATE'), findsOneWidget);
      expect(find.text('Продвинуть'), findsNothing);
      expect(find.byType(Switch), findsNothing);
    });
  });

  test('Settings → Strategies lazily prefetches comparison once', () async {
    final engine = _ComparisonEngine(const ExperimentComparison.noExperiments());
    final controller = AppController(
      DemoRepository(),
      engine: engine,
      thinMode: true,
    );
    addTearDown(controller.dispose);

    controller.goSection(AppSection.settings);
    expect(engine.calls, 0);

    controller.goPill(SettingsPill.strategies.index);
    await Future<void>.delayed(Duration.zero);

    expect(engine.calls, 1);
    expect(controller.experimentComparison?.status,
        ExperimentComparisonStatus.noExperiments);
  });
}
