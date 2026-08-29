import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/control_dashboard_client.dart';
import 'package:signalai/ui/screens/server_control_screen.dart';

class _ControlApi extends ApiClient {
  _ControlApi(this.response)
      : super(baseUrl: 'https://engine.test', deviceToken: 'device-test');

  final Map<String, dynamic> response;
  final List<String> paths = [];

  @override
  Future<Map<String, dynamic>> get(String path) async {
    paths.add(path);
    return response;
  }
}

Map<String, dynamic> _payload({
  String venue = 'BYBIT',
  String health = 'BROKEN_INPUT',
  bool brokenCarry = true,
  bool pendingMomentum = false,
  bool withBacktest = true,
  bool withRisk = true,
  int presented = 1,
}) {
  final candidates = <Map<String, dynamic>>[];
  if (brokenCarry) {
    candidates.add({
      'version': 'crypto_carry_v1',
      'verdict': 'BROKEN_INPUT',
      'shadow': {
        'version': 'crypto_carry_v1',
        'observations': 3,
        'evaluated': 0,
        'unavailable': 3,
        'emitted': 0,
        'top_unavailable_reasons': [
          {'reason': 'BYBIT_CARRY_FACTS_UNAVAILABLE', 'count': 3},
        ],
      },
      'paper': {
        'control': {
          'decisions': 0,
          'emitted': 0,
          'evaluated_outcomes': 0,
          'pending_outcomes': 0,
          'unavailable_outcomes': 0,
          'mean_net_r': null,
        },
        'candidate': {
          'decisions': 0,
          'emitted': 0,
          'evaluated_outcomes': 0,
          'pending_outcomes': 0,
          'unavailable_outcomes': 0,
          'mean_net_r': null,
        },
        'comparable_pairs': 0,
        'control_mean_net_r': null,
        'candidate_mean_net_r': null,
        'delta_mean_net_r': null,
      },
    });
  }
  if (pendingMomentum) {
    candidates.add({
      'version': 'momentum_v2',
      'verdict': 'INSUFFICIENT_OUTCOMES',
      'shadow': {
        'version': 'momentum_v2',
        'observations': 10,
        'evaluated': 10,
        'unavailable': 0,
        'emitted': 2,
        'top_unavailable_reasons': <Map<String, dynamic>>[],
      },
      'paper': {
        'control': {
          'decisions': 1,
          'emitted': 1,
          'evaluated_outcomes': 1,
          'pending_outcomes': 0,
          'unavailable_outcomes': 0,
          'mean_net_r': 1.0,
        },
        'candidate': {
          'decisions': 1,
          'emitted': 1,
          'evaluated_outcomes': 0,
          'pending_outcomes': 1,
          'unavailable_outcomes': 0,
          'mean_net_r': null,
        },
        'comparable_pairs': 0,
        'control_mean_net_r': null,
        'candidate_mean_net_r': null,
        'delta_mean_net_r': null,
      },
    });
  }

  return {
    'generated_at': '2026-08-29T10:00:00+00:00',
    'venue': venue,
    'window_hours': 168,
    'health': health,
    'funnel': {
      'control': {
        'ideas_created': 4,
        'presented': presented,
        'statuses': {'WATCH': 3, 'TRIGGERED': 1},
        'qualities': {'WATCH': 3, 'ACTIVE': 1},
        'versions': {'legacy_control_v1': 4},
      },
      'candidates': [for (final row in candidates) row['shadow']],
    },
    'competition': {
      'control_version': 'legacy_control_v1',
      'min_comparable_sample': 40,
      'candidates': candidates,
    },
    'backtest': {
      'latest': withBacktest
          ? {
              'id': 'backtest-1',
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
              'config_hash': 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
              'engine_version': '2.0.0',
              'universe': ['CRYPTO'],
              'created_at': '2026-08-29T09:00:00+00:00',
            }
          : null,
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
      'live_gate': {'min_paper_trades': 100, 'min_paper_days': 60},
    },
    'risk_optimizer': {
      'champion': withRisk
          ? {
              'version': '20260820T120000Z',
              'candidate_id': 'runner_wide',
              'algorithm': 'bounded_walk_forward_llm_critic_v2',
              'sample_size': 96,
              'trained_from': '2026-01-01',
              'trained_to': '2026-08-20',
              'promoted_at': '2026-08-27T10:00:00+00:00',
              'metrics': {'expectancy': '0.21', 'max_drawdown': '4.1'},
              'llm_review': {'verdict': 'pass', 'summary': 'stable'},
              'absolute_risk_caps_changed': false,
            }
          : null,
      'latest_run': withRisk
          ? {
              'id': 'risk-run-1',
              'label': 'risk-exit-v2:runner_wide',
              'strategy': null,
              'period_from': '2026-01-01',
              'period_to': '2026-08-20',
              'trades': 96,
              'net_return': 8.0,
              'profit_factor': null,
              'expectancy_r': 0.21,
              'max_drawdown': 4.1,
              'sharpe': null,
              'sortino': null,
              'calmar': null,
              'brier_score': null,
              'pbo': null,
              'top5_contribution': 0.24,
              'gate_passed': true,
              'gate_detail': {'promotion': 'passed'},
              'report': {'candidate_id': 'runner_wide'},
              'config_hash': 'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
              'engine_version': '2.0.0',
              'universe': ['FORTS', 'CRYPTO'],
              'created_at': '2026-08-27T10:00:00+00:00',
            }
          : null,
      'next_due_at': '2026-09-03T10:00:00+00:00',
      'config': {
        'cadence_days': 7,
        'min_samples': 80,
        'min_oos_expectancy_improvement_r': 0.03,
        'candidate_ids': ['baseline', 'runner_wide', 'harvest_early'],
        'absolute_risk_caps_mutable': false,
      },
    },
  };
}

Future<void> _pumpControl(
  WidgetTester tester, {
  required ControlDashboardLoader loader,
}) async {
  tester.view.physicalSize = const Size(412, 892);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(body: ServerControlScreen(loader: loader)),
    ),
  );
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
}

void main() {
  group('ControlDashboardClient', () {
    test('loads the exact BYBIT read-only snapshot and preserves null evidence',
        () async {
      final api = _ControlApi(_payload(pendingMomentum: true));
      final value = await ControlDashboardClient(api).load(venue: 'BYBIT');

      expect(api.paths,
          ['/api/v1/control/dashboard?venue=BYBIT&window_hours=168']);
      expect(value.venue, 'BYBIT');
      final momentum = value.competition.candidates
          .firstWhere((row) => row.version == 'momentum_v2');
      expect(momentum.paper.candidate.meanNetR, isNull);
      expect(momentum.paper.candidateMeanNetR, isNull);
      expect(momentum.paper.comparablePairs, 0);
    });

    test('fails closed if server returns another venue', () async {
      final api = _ControlApi(_payload(venue: 'FORTS'));

      expect(
        () => ControlDashboardClient(api).load(venue: 'BYBIT'),
        throwsA(isA<FormatException>()),
      );
    });
  });

  group('ServerControlScreen', () {
    testWidgets('surfaces BROKEN INPUT and the actual unavailable reason',
        (tester) async {
      await _pumpControl(
        tester,
        loader: (_) async => _payload().toControlDashboard(),
      );

      expect(find.text('BROKEN INPUT'), findsWidgets);
      expect(find.text('crypto_carry_v1'), findsWidgets);
      expect(find.textContaining('BYBIT_CARRY_FACTS_UNAVAILABLE'), findsOneWidget);
      expect(find.textContaining('0 / 3 обработано'), findsOneWidget);
    });

    testWidgets('pending Paper A/B stays pending instead of becoming zero R',
        (tester) async {
      await _pumpControl(
        tester,
        loader: (_) async => _payload(
          health: 'DEGRADED',
          brokenCarry: false,
          pendingMomentum: true,
        ).toControlDashboard(),
      );

      expect(find.text('momentum_v2'), findsWidgets);
      expect(find.textContaining('Сравнимых исходов пока нет'), findsOneWidget);
      expect(find.textContaining('кандидат: —'), findsOneWidget);
      expect(find.textContaining('кандидат: 0,00R'), findsNothing);
    });

    testWidgets('backtest OOS is visible as distinct evidence', (tester) async {
      await _pumpControl(
        tester,
        loader: (_) async => _payload(brokenCarry: false, health: 'OK')
            .toControlDashboard(),
      );

      expect(find.text('БЭКТЕСТ / OOS'), findsOneWidget);
      expect(find.text('crypto-oos-main'), findsOneWidget);
      expect(find.textContaining('PF 1,42'), findsOneWidget);
      expect(find.textContaining('0,18R'), findsOneWidget);
    });

    testWidgets('switching venue requests a new strict FORTS snapshot',
        (tester) async {
      final calls = <String>[];
      Future<ControlDashboardSnapshot> loader(String venue) async {
        calls.add(venue);
        return _payload(
          venue: venue,
          brokenCarry: false,
          health: 'OK',
          presented: venue == 'FORTS' ? 7 : 1,
        ).toControlDashboard();
      }

      await _pumpControl(tester, loader: loader);
      expect(calls, ['BYBIT']);
      expect(find.textContaining('Показано 1'), findsOneWidget);

      await tester.tap(find.text('FORTS'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(calls, ['BYBIT', 'FORTS']);
      expect(find.textContaining('Показано 7'), findsOneWidget);
    });
  });
}

extension on Map<String, dynamic> {
  ControlDashboardSnapshot toControlDashboard() =>
      ControlDashboardSnapshot.fromJson(this);
}
