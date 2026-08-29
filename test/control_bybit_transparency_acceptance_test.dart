import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/control_dashboard_client.dart';
import 'package:signalai/ui/screens/server_control_screen.dart';

ControlDashboardSnapshot _bybitSnapshot() => ControlDashboardSnapshot.fromJson({
      'generated_at': '2026-08-29T21:00:00+00:00',
      'venue': 'BYBIT',
      'window_hours': 168,
      'health': 'DEGRADED',
      'runtime_roles': {
        'live_generator': {
          'version': 'legacy_control_v1',
          'publishes_trade_ideas': true,
          'strategy_families': [
            'TREND_PULLBACK',
            'BREAKOUT_RETEST',
            'WYCKOFF_REVERSAL',
          ],
        },
        'champion': null,
        'challengers': ['momentum_v2', 'breakout_v2'],
        'shadow_only': ['momentum_v2', 'breakout_v2'],
        'governance_controls_runtime': false,
        'explanation':
            'StrategyRegistry roles are governance/measurement state; current TradeIdea publication remains on the legacy production scanner.',
      },
      'funnel': {
        'control': {
          'ideas_created': 1,
          'presented': 1,
          'statuses': {'TRIGGERED': 1},
          'qualities': {'ACTIVE': 1},
          'versions': {'legacy_control_v1': 1},
        },
        'scan': {
          'universe': 30,
          'data_healthy': 28,
          'liquid': 23,
          'regime_eligible': 11,
          'strategy_evaluated': 11,
          'setup_reject': 7,
          'cost_rr_reject': 3,
          'published': 1,
          'terminal': {
            'DATA_BLOCKED': 2,
            'LIQUIDITY_BLOCKED': 5,
            'SETUP_REJECTED': 7,
            'ADMISSION_REJECTED': 15,
            'PUBLISHED': 1,
          },
          'top_reasons': [
            {'reason': 'NO_VALID_SETUP', 'count': 7},
            {'reason': 'LIQUIDITY_UNTRADEABLE', 'count': 5},
            {'reason': 'RR', 'count': 3},
          ],
        },
        'candidates': <Map<String, dynamic>>[],
      },
      'competition': {
        'control_version': 'legacy_control_v1',
        'min_comparable_sample': 40,
        'candidates': <Map<String, dynamic>>[],
      },
      'data_readiness': {
        'status': 'DATA_BLOCKED',
        'symbols': [
          {
            'symbol': 'BTCUSDT',
            'status': 'DATA_READY',
            'snapshot_id': 'a' * 64,
            'content_sha256': 'b' * 64,
            'tradable_at': '2026-08-29T20:00:00+00:00',
            'row_count': 10000,
            'coverage': [
              {'stream': 'klines', 'ready': true, 'reason': 'READY'},
              {
                'stream': 'open_interest',
                'ready': true,
                'reason': 'READY'
              },
            ],
          },
          {
            'symbol': 'ETHUSDT',
            'status': 'DATA_BLOCKED',
            'snapshot_id': 'c' * 64,
            'content_sha256': 'd' * 64,
            'tradable_at': '2026-08-29T20:00:00+00:00',
            'row_count': 8000,
            'coverage': [
              {
                'stream': 'open_interest',
                'ready': false,
                'reason': 'HISTORY_LT_36M'
              },
            ],
          },
        ],
      },
      'backtest': {
        'latest': null,
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
        'champion': null,
        'latest_run': null,
        'next_due_at': null,
        'scheduled': true,
        'config': {
          'cadence_days': 7,
          'min_samples': 80,
          'min_oos_expectancy_improvement_r': 0.03,
          'candidate_ids': ['baseline'],
          'absolute_risk_caps_mutable': false,
        },
      },
    });

Future<void> _pump(WidgetTester tester) async {
  // This suite verifies the evidence contract, not scrolling mechanics. A tall
  // viewport builds the complete lazy ListView so deep Control sections can be
  // asserted without coupling the acceptance test to Scrollable internals.
  tester.view.physicalSize = const Size(412, 8000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: ServerControlScreen(loader: (_) async => _bybitSnapshot()),
      ),
    ),
  );
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  testWidgets('BYBIT Control names the live generator and shadow-only candidates',
      (tester) async {
    await _pump(tester);

    expect(find.textContaining('LIVE GENERATOR'), findsOneWidget);
    expect(find.textContaining('legacy_control_v1'), findsWidgets);
    expect(find.textContaining('SHADOW ONLY'), findsOneWidget);
    expect(find.textContaining('momentum_v2'), findsOneWidget);
  });

  testWidgets('BYBIT Control exposes scan funnel and machine-readable reasons',
      (tester) async {
    await _pump(tester);

    expect(find.textContaining('30 → 28 → 23 → 11'), findsOneWidget);
    expect(find.textContaining('published 1'), findsOneWidget);
    expect(find.textContaining('NO_VALID_SETUP'), findsOneWidget);
    expect(find.textContaining('LIQUIDITY_UNTRADEABLE'), findsOneWidget);
  });

  testWidgets('BYBIT Control shows per-symbol 36m dataset blockers',
      (tester) async {
    await _pump(tester);

    expect(find.textContaining('DATA BLOCKED'), findsWidgets);
    expect(find.textContaining('BTCUSDT'), findsOneWidget);
    expect(find.textContaining('ETHUSDT'), findsOneWidget);
    expect(find.textContaining('HISTORY_LT_36M'), findsOneWidget);
  });
}
