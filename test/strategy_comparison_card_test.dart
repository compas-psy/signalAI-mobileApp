import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/widgets/strategy_comparison_card.dart';

void main() {
  const summaryJson = <String, dynamic>{
    'id': 'exp-1',
    'name': 'trend-v2 vs legacy',
    'control': <String, dynamic>{
      'family': 'TREND_PULLBACK',
      'version': 'legacy_control_v1',
    },
    'candidate': <String, dynamic>{
      'family': 'TREND_PULLBACK',
      'version': 'candidate_trend_v2',
    },
    'stage': 'OOS',
    'dataset_name': 'short_horizon_features',
    'latest_run': <String, dynamic>{
      'id': 'run-1',
      'evaluated_at': '2026-08-18T12:00:00+00:00',
      'sample_size': 120,
      'sample_adequate': true,
    },
  };

  const comparisonJson = <String, dynamic>{
    'experiment': <String, dynamic>{
      'id': 'exp-1',
      'name': 'trend-v2 vs legacy',
      'control_family': 'TREND_PULLBACK',
      'control_version': 'legacy_control_v1',
      'candidate_family': 'TREND_PULLBACK',
      'candidate_version': 'candidate_trend_v2',
    },
    'evidence': <String, dynamic>{
      'dataset_name': 'short_horizon_features',
      'dataset_snapshot_id': 'aaaaaaaa',
      'stage': 'OOS',
      'same_data_hash': 'bbbbbbbb',
      'cost_model_hash': 'cccccccc',
    },
    'latest_run': <String, dynamic>{
      'id': 'run-1',
      'evaluated_at': '2026-08-18T12:00:00+00:00',
      'sample_size': 120,
      'sample_adequate': true,
      'result': <String, dynamic>{
        'incremental_control_delta': <String, dynamic>{
          'incremental_net_expectancy_r': 0.14,
          'hit_rate_delta': 0.08,
          'calibration_error_delta': -0.03,
          'opportunity_overlap': 0.72,
        },
      },
    },
    'metrics': <Map<String, dynamic>>[
      <String, dynamic>{
        'name': 'net_expectancy_r',
        'control_value': 0.10,
        'candidate_value': 0.14,
        'delta': 0.04,
        'unit': 'R',
      },
      <String, dynamic>{
        'name': 'max_drawdown_r',
        'control_value': 0.70,
        'candidate_value': 0.40,
        'delta': -0.30,
        'unit': 'R',
      },
    ],
    'latest_decision': <String, dynamic>{
      'decision': 'KEEP_CANDIDATE',
      'source': 'OWNER',
      'actor': 'owner',
      'reason': 'continue to shadow',
      'detail': <String, dynamic>{'next_stage': 'SHADOW'},
      'decided_at': '2026-08-18T12:03:00+00:00',
    },
  };

  test('parses comparison evidence and canonical deltas', () {
    final view = StrategyComparisonView.fromApi(
      summary: summaryJson,
      comparison: comparisonJson,
    );

    expect(view.controlVersion, 'legacy_control_v1');
    expect(view.candidateVersion, 'candidate_trend_v2');
    expect(view.stage, 'OOS');
    expect(view.sampleSize, 120);
    expect(view.sampleAdequate, isTrue);
    expect(view.netExpectancyDelta, closeTo(0.04, 1e-9));
    expect(view.maxDrawdownDelta, closeTo(-0.30, 1e-9));
    expect(view.hitRateDelta, closeTo(0.08, 1e-9));
    expect(view.calibrationErrorDelta, closeTo(-0.03, 1e-9));
    expect(view.opportunityOverlap, closeTo(0.72, 1e-9));
    expect(view.latestDecision, 'KEEP_CANDIDATE');
  });

  testWidgets('shows empty state when there are no experiments', (tester) async {
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: StrategyComparisonCard(
          load: () async => null,
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Контроль vs кандидат'), findsOneWidget);
    expect(find.textContaining('Экспериментов пока нет'), findsOneWidget);
    expect(find.text('Продвинуть кандидата'), findsNothing);
  });

  testWidgets('shows comparison metrics without promotion controls', (tester) async {
    final view = StrategyComparisonView.fromApi(
      summary: summaryJson,
      comparison: comparisonJson,
    );
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: StrategyComparisonCard(load: () async => view),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('legacy_control_v1'), findsOneWidget);
    expect(find.text('candidate_trend_v2'), findsOneWidget);
    expect(find.textContaining('120 наблюдений'), findsOneWidget);
    expect(find.textContaining('KEEP_CANDIDATE'), findsOneWidget);
    expect(find.textContaining('+0,040 R'), findsOneWidget);
    expect(find.textContaining('−0,300 R'), findsOneWidget);
    expect(find.textContaining('72%'), findsOneWidget);
    expect(find.text('Продвинуть кандидата'), findsNothing);
    expect(find.byType(EditableText), findsNothing);
  });

  testWidgets('marks an inadequate sample explicitly', (tester) async {
    final view = StrategyComparisonView.fromApi(
      summary: <String, dynamic>{
        ...summaryJson,
        'latest_run': <String, dynamic>{
          ...(summaryJson['latest_run']! as Map<String, dynamic>),
          'sample_adequate': false,
          'sample_size': 18,
        },
      },
      comparison: <String, dynamic>{
        ...comparisonJson,
        'latest_run': <String, dynamic>{
          ...(comparisonJson['latest_run']! as Map<String, dynamic>),
          'sample_adequate': false,
          'sample_size': 18,
        },
      },
    );
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: StrategyComparisonCard(load: () async => view),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('Данных пока недостаточно'), findsOneWidget);
  });
}
