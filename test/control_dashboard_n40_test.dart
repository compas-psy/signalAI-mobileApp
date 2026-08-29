import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/control_dashboard_client.dart';
import 'package:signalai/ui/screens/server_control_screen.dart';

Map<String, dynamic> _payload() => {
      'generated_at': '2026-08-29T12:00:00+00:00',
      'venue': 'BYBIT',
      'window_hours': 168,
      'health': 'DEGRADED',
      'funnel': {
        'control': {
          'ideas_created': 1,
          'presented': 1,
          'statuses': {'ACTIVE': 1},
          'qualities': {'ACTIVE': 1},
          'versions': {'legacy_control_v1': 1},
        },
        'candidates': [
          {
            'version': 'momentum_v2',
            'observations': 5,
            'evaluated': 5,
            'unavailable': 0,
            'emitted': 1,
            'top_unavailable_reasons': <Map<String, dynamic>>[],
          }
        ],
      },
      'competition': {
        'control_version': 'legacy_control_v1',
        'min_comparable_sample': 40,
        'candidates': [
          {
            'version': 'momentum_v2',
            'verdict': 'WAITING_FOR_SAMPLE',
            'shadow': {
              'version': 'momentum_v2',
              'observations': 5,
              'evaluated': 5,
              'unavailable': 0,
              'emitted': 1,
              'top_unavailable_reasons': <Map<String, dynamic>>[],
            },
            'paper': {
              'control': {
                'decisions': 1,
                'emitted': 1,
                'evaluated_outcomes': 1,
                'pending_outcomes': 0,
                'unavailable_outcomes': 0,
                'mean_net_r': 0.10,
              },
              'candidate': {
                'decisions': 1,
                'emitted': 1,
                'evaluated_outcomes': 1,
                'pending_outcomes': 0,
                'unavailable_outcomes': 0,
                'mean_net_r': 0.50,
              },
              'comparable_pairs': 1,
              'required_pairs': 40,
              'remaining_pairs': 39,
              'sample_adequate': false,
              'control_mean_net_r': 0.10,
              'candidate_mean_net_r': 0.50,
              'delta_mean_net_r': 0.40,
            },
          }
        ],
      },
      'backtest': {
        'latest': null,
        'walk_forward': <String, dynamic>{},
        'paper_gate': <String, dynamic>{},
        'live_gate': <String, dynamic>{},
      },
      'risk_optimizer': {
        'champion': null,
        'latest_run': null,
        'next_due_at': null,
        'config': {
          'cadence_days': 7,
          'min_samples': 80,
          'min_oos_expectancy_improvement_r': 0.03,
          'candidate_ids': <String>[],
          'absolute_risk_caps_mutable': false,
        },
      },
    };

void main() {
  testWidgets('competition shows exact N/40 and remaining pairs for selected venue',
      (tester) async {
    tester.view.physicalSize = const Size(412, 892);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final snapshot = ControlDashboardSnapshot.fromJson(_payload());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ServerControlScreen(loader: (_) async => snapshot),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));

    await tester.drag(find.byType(ListView), const Offset(0, -450));
    await tester.pumpAndSettle();

    expect(find.textContaining('N 1/40'), findsOneWidget);
    expect(find.textContaining('осталось 39'), findsOneWidget);
    expect(find.text('КАНДИДАТ ЛУЧШЕ'), findsNothing);
  });
}
