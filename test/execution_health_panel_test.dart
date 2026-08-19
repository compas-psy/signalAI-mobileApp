import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/ui/widgets/execution_health_panel.dart';

Map<String, dynamic> _health() => {
      'items': [
        {
          'intent_id': '11111111-1111-1111-1111-111111111111',
          'idea_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
          'instrument_id': 'MOEX:FUT:GAZP',
          'venue': 'MOEX',
          'account': 'sandbox-main',
          'state': 'PROTECTED',
          'decision_to_intent_ms': 250,
          'submit_to_ack_ms': 120,
          'fill_deviation_bps': '25.00',
          'protection_arm_ms': 30500,
          'protection_sla_ms': 30000,
          'reconciliation_mismatch_count': 1,
          'websocket_state': 'STALE',
          'websocket_stale': true,
          'rejected_order_count': 1,
          'duplicate_prevention_count': 2,
          'violations': [
            {
              'code': 'PROTECTION_ARM_SLO',
              'label': 'Защита поставлена позже SLA',
              'detail': '30500 ms > 30000 ms from first fill',
            },
            {
              'code': 'RECONCILIATION_MISMATCH',
              'label': 'Сверка не совпала с ожидаемым состоянием',
              'detail': '1 reconciliation mismatch event(s)',
            },
          ],
        },
        {
          'intent_id': '22222222-2222-2222-2222-222222222222',
          'idea_id': 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
          'instrument_id': 'BYBIT:BTCUSDT',
          'venue': 'BYBIT',
          'account': 'paper-main',
          'state': 'INTENT_CREATED',
          'decision_to_intent_ms': 80,
          'submit_to_ack_ms': null,
          'fill_deviation_bps': null,
          'protection_arm_ms': null,
          'protection_sla_ms': 30000,
          'reconciliation_mismatch_count': 0,
          'websocket_state': 'NOT_CONFIGURED',
          'websocket_stale': null,
          'rejected_order_count': 0,
          'duplicate_prevention_count': 0,
          'violations': <dynamic>[],
        },
      ],
      'aggregate': {
        'total_intents': 2,
        'violation_intents': 1,
        'protection_slo_breaches': 1,
        'reconciliation_mismatches': 1,
        'websocket_configured_intents': 1,
        'websocket_stale_intents': 1,
        'rejected_orders': 1,
        'duplicate_preventions': 2,
      },
    };

void main() {
  testWidgets('execution violations stay visible on their exact trade row',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: ExecutionHealthPanel(data: _health()),
          ),
        ),
      ),
    );

    expect(find.text('ЗДОРОВЬЕ ИСПОЛНЕНИЯ'), findsOneWidget);
    expect(find.textContaining('1 из 2 с нарушениями'), findsOneWidget);
    expect(find.textContaining('MOEX:FUT:GAZP'), findsOneWidget);
    expect(find.text('Защита поставлена позже SLA'), findsOneWidget);
    expect(find.text('Сверка не совпала с ожидаемым состоянием'), findsOneWidget);
    expect(find.textContaining('decision→intent 250 мс'), findsOneWidget);
    expect(find.textContaining('submit→ack 120 мс'), findsOneWidget);
    expect(find.textContaining('fill +25,00 bp'), findsOneWidget);
    expect(find.textContaining('protection 30,5 c / SLA 30,0 c'), findsOneWidget);
    expect(find.textContaining('reconcile 1'), findsOneWidget);
    expect(find.textContaining('reject 1'), findsOneWidget);
    expect(find.textContaining('dedupe 2'), findsOneWidget);
    expect(find.textContaining('WS STALE'), findsOneWidget);
  });

  testWidgets('missing websocket adapter evidence is not rendered as healthy',
      (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: ExecutionHealthPanel(data: _health()),
          ),
        ),
      ),
    );

    expect(find.textContaining('BYBIT:BTCUSDT'), findsOneWidget);
    expect(find.textContaining('WS NOT_CONFIGURED'), findsOneWidget);
    expect(find.textContaining('WS HEALTHY'), findsNothing);
  });
}
