import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/execution_timeline_client.dart';
import 'package:signalai/domain/idea/execution.dart';
import 'package:signalai/ui/widgets/execution_strip.dart';
import 'package:signalai/ui/widgets/execution_timeline_card.dart';

class _FakeTimelineClient extends ExecutionTimelineClient {
  _FakeTimelineClient(this.timeline)
      : super(
          api: ApiClient(
            baseUrl: 'https://engine.test',
            deviceToken: 'device',
          ),
        );

  final ExecutionTimeline timeline;
  final List<String> calls = <String>[];

  @override
  Future<ExecutionTimeline> fetch({required String ideaId}) async {
    calls.add(ideaId);
    return timeline;
  }
}

ExecutionTimeline _timeline() => ExecutionTimeline(
      ideaId: 'idea-51',
      intentIds: const <String>['intent-51'],
      events: <ExecutionTimelineEvent>[
        ExecutionTimelineEvent(
          source: 'fill',
          kind: 'FILL_RECORDED',
          occurredAt: DateTime.utc(2026, 8, 20, 14, 15),
          facts: const <String, dynamic>{
            'quantity': '1.250000000000',
            'price': '90110.000000000000',
          },
        ),
        ExecutionTimelineEvent(
          source: 'manual_control',
          kind: 'MANUAL_CLOSE_REQUESTED',
          occurredAt: DateTime.utc(2026, 8, 20, 14, 16),
          facts: const <String, dynamic>{
            'status': 'REQUESTED',
            'reduce_only': true,
          },
        ),
      ],
    );

void main() {
  testWidgets('SAI-051 timeline is lazy and does not hit server before owner asks',
      (tester) async {
    final client = _FakeTimelineClient(_timeline());
    await tester.pumpWidget(
      _host(ExecutionTimelineCard(ideaId: 'idea-51', client: client)),
    );

    expect(find.text('История исполнения'), findsOneWidget);
    expect(find.text('Показать историю'), findsOneWidget);
    expect(client.calls, isEmpty);
  });

  testWidgets('SAI-051 timeline renders durable facts without inventing execution',
      (tester) async {
    final client = _FakeTimelineClient(_timeline());
    await tester.pumpWidget(
      _host(ExecutionTimelineCard(ideaId: 'idea-51', client: client)),
    );

    await tester.tap(find.text('Показать историю'));
    await tester.pumpAndSettle();

    expect(client.calls, <String>['idea-51']);
    expect(find.text('Исполнение по заявке'), findsOneWidget);
    expect(find.textContaining('1.250000000000'), findsOneWidget);
    expect(find.text('Команда закрыть зафиксирована'), findsOneWidget);
    expect(find.textContaining('Биржа ещё не подтвердила исполнение'), findsOneWidget);
    expect(find.text('Сделка закрыта'), findsNothing);
    expect(find.text('Исполнено'), findsNothing);
  });

  testWidgets('SAI-051 execution strip keeps forensic history available when closed',
      (tester) async {
    await tester.pumpWidget(
      _host(
        ExecutionStrip(
          execution: _execution(ExecutionState.closed),
          now: DateTime.utc(2026, 8, 20, 15),
        ),
      ),
    );

    expect(find.text('История исполнения'), findsOneWidget);
    expect(find.text('Показать историю'), findsOneWidget);
  });
}

Widget _host(Widget child) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SizedBox(width: 420, child: child),
        ),
      ),
    );

Execution _execution(ExecutionState state) => Execution(
      ideaId: 'idea-51',
      planHash: 'plan-51',
      plannedQuantity: 4,
      state: state,
      protection: ProtectionStatus.placed,
      fills: <Fill>[
        Fill(
          quantity: 4,
          price: 90000,
          at: DateTime.utc(2026, 8, 20, 14),
        ),
      ],
      entryFilledAt: DateTime.utc(2026, 8, 20, 14),
    );
