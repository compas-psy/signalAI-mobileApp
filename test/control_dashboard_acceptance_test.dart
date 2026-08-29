import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/control_dashboard_client.dart';
import 'package:signalai/ui/screens/server_control_screen.dart';

ControlDashboardSnapshot _snapshot() => ControlDashboardSnapshot.fromJson({
      'generated_at': '2026-08-29T12:00:00+00:00',
      'venue': 'BYBIT',
      'window_hours': 168,
      'health': 'OK',
      'funnel': {
        'control': {
          'ideas_created': 4,
          'presented': 1,
          'statuses': {'WATCH': 3, 'TRIGGERED': 1},
          'qualities': {'WATCH': 3, 'ACTIVE': 1},
          'versions': {'legacy_control_v1': 4},
        },
        'candidates': <Map<String, dynamic>>[],
      },
      'competition': {
        'control_version': 'legacy_control_v1',
        'min_comparable_sample': 40,
        'candidates': <Map<String, dynamic>>[],
      },
      'backtest': {
        'latest': {
          'id': 'run-1',
          'label': 'crypto-oos-main',
          'strategy': 'TREND_PULLBACK',
          'period_from': '2025-01-01',
          'period_to': '2026-08-01',
          'trades': 240,
          'net_return': 14.0,
          'profit_factor': 1.42,
          'expectancy_r': 0.18,
          'max_drawdown': 3.2,
          'sharpe': 1.10,
          'sortino': 1.35,
          'calmar': 1.20,
          'brier_score': 0.19,
          'pbo': 0.12,
          'top5_contribution': 0.22,
          'gate_passed': true,
          'gate_detail': {'reason': 'passed'},
          'report': {'stage': 'OOS'},
          'config_hash': 'c'.padLeft(64, 'c'),
          'engine_version': '2.0.0',
          'universe': ['CRYPTO'],
          'created_at': '2026-08-29T11:00:00+00:00',
        },
        'walk_forward': {
          'min_history_months': 36,
          'train_months': 24,
          'validation_months': 6,
          'test_months': 3,
          'step_months': 3,
        },
        'paper_gate': {
          'min_aggregate_trades': 200,
          'min_trades_per_setup': 40,
          'min_oos_profit_factor': 1.20,
          'min_oos_expectancy_r': 0.12,
          'max_top5_contribution': 0.30,
        },
        'live_gate': {
          'min_paper_trades': 100,
          'min_paper_days': 60,
        },
      },
      'risk_optimizer': {
        'champion': {
          'version': 'risk-v1',
          'candidate_id': 'runner_wide',
          'algorithm': 'bounded_walk_forward_llm_critic_v2',
          'sample_size': 96,
          'trained_from': '2026-01-01',
          'trained_to': '2026-08-20',
          'promoted_at': '2026-08-27T10:00:00+00:00',
          'metrics': {'expectancy': '0.21'},
          'llm_review': {'verdict': 'pass'},
          'absolute_risk_caps_changed': false,
        },
        'latest_run': null,
        'next_due_at': '2026-09-03T10:00:00+00:00',
        'config': {
          'cadence_days': 7,
          'min_samples': 80,
          'min_oos_expectancy_improvement_r': 0.03,
          'candidate_ids': ['baseline', 'runner_wide', 'harvest_early'],
          'absolute_risk_caps_mutable': false,
        },
      },
    });

Future<void> _pump(
  WidgetTester tester,
  Future<ControlDashboardSnapshot> Function(String venue) loader,
) async {
  tester.view.physicalSize = const Size(412, 892);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(home: Scaffold(body: ServerControlScreen(loader: loader))),
  );
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
}

Future<void> _scrollTo(WidgetTester tester, Finder target) async {
  for (var i = 0; i < 20 && target.evaluate().isEmpty; i++) {
    await tester.drag(find.byType(ListView), const Offset(0, -260));
    await tester.pump(const Duration(milliseconds: 50));
  }
  await tester.ensureVisible(target);
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  testWidgets('owner can explicitly refresh the read-only snapshot',
      (tester) async {
    var calls = 0;
    await _pump(tester, (_) async {
      calls += 1;
      return _snapshot();
    });

    expect(calls, 1);
    expect(find.text('Обновить'), findsOneWidget);
    await tester.tap(find.text('Обновить'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    expect(calls, 2);
  });

  testWidgets('backtest exposes configured Paper and Live promotion gates',
      (tester) async {
    await _pump(tester, (_) async => _snapshot());

    await _scrollTo(tester, find.textContaining('Paper gate'));
    expect(find.textContaining('Paper gate'), findsOneWidget);
    expect(find.textContaining('PF ≥ 1,20'), findsOneWidget);
    expect(find.textContaining('expectancy ≥ 0,12R'), findsOneWidget);
    expect(find.textContaining('Live gate'), findsOneWidget);
    expect(find.textContaining('N ≥ 100'), findsOneWidget);
    expect(find.textContaining('days ≥ 60'), findsOneWidget);
  });

  testWidgets('risk optimizer exposes the bounded candidate set',
      (tester) async {
    await _pump(tester, (_) async => _snapshot());

    await _scrollTo(tester, find.textContaining('baseline'));
    expect(
      find.textContaining('baseline · runner_wide · harvest_early'),
      findsOneWidget,
    );
  });
}
