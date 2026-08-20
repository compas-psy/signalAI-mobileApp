import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/manual_trade_control_client.dart';
import 'package:signalai/domain/idea/execution.dart';
import 'package:signalai/ui/widgets/execution_strip.dart';
import 'package:signalai/ui/widgets/manual_trade_controls.dart';

class _Call {
  const _Call({
    required this.ideaId,
    required this.action,
    required this.reason,
    required this.idempotencyKey,
    this.quantity,
    this.stopPrice,
  });

  final String ideaId;
  final ManualTradeAction action;
  final String reason;
  final String idempotencyKey;
  final String? quantity;
  final String? stopPrice;
}

class _FakeClient extends ManualTradeControlClient {
  _FakeClient()
      : super(
          api: ApiClient(
            baseUrl: 'https://engine.test',
            deviceToken: 'device',
          ),
        );

  final List<_Call> calls = <_Call>[];

  @override
  Future<ManualTradeControlResult> request({
    required String ideaId,
    required ManualTradeAction action,
    required String reason,
    required String idempotencyKey,
    String? quantity,
    String? stopPrice,
  }) async {
    calls.add(
      _Call(
        ideaId: ideaId,
        action: action,
        reason: reason,
        idempotencyKey: idempotencyKey,
        quantity: quantity,
        stopPrice: stopPrice,
      ),
    );
    return ManualTradeControlResult(
      commandId: 'command-${calls.length}',
      intentId: 'intent-50',
      managementPolicySnapshotId: 'policy-49',
      action: action,
      status: action == ManualTradeAction.returnAuto ? 'COMPLETED' : 'REQUESTED',
      reduceOnly: true,
      quantity: quantity,
      stopPrice: stopPrice,
      orderId: action == ManualTradeAction.returnAuto ? null : 'order-50',
      orderStatus: action == ManualTradeAction.returnAuto ? null : 'REQUESTED',
      created: true,
    );
  }
}

void main() {
  testWidgets('SAI-050 protected trade exposes exactly the four safe owner actions',
      (tester) async {
    await tester.pumpWidget(_host(
      ExecutionStrip(
        execution: _execution(ExecutionState.active),
        now: DateTime.utc(2026, 8, 20, 15),
      ),
    ));

    expect(find.text('Закрыть'), findsOneWidget);
    expect(find.text('Сократить'), findsOneWidget);
    expect(find.text('Подтянуть стоп'), findsOneWidget);
    expect(find.text('Вернуть автоматическое сопровождение'), findsOneWidget);
    expect(find.textContaining('увеличить'), findsNothing);
    expect(find.textContaining('расширить стоп'), findsNothing);
  });

  testWidgets('SAI-050 unprotected execution does not expose manual trade controls',
      (tester) async {
    await tester.pumpWidget(_host(
      ExecutionStrip(
        execution: _execution(ExecutionState.entryFilled),
        now: DateTime.utc(2026, 8, 20, 15),
      ),
    ));

    expect(find.text('Закрыть'), findsNothing);
    expect(find.text('Сократить'), findsNothing);
    expect(find.text('Подтянуть стоп'), findsNothing);
    expect(find.text('Вернуть автоматическое сопровождение'), findsNothing);
  });

  testWidgets('SAI-050 close needs a second explicit tap and never mutates local execution',
      (tester) async {
    final client = _FakeClient();
    await tester.pumpWidget(_host(
      ManualTradeControls(ideaId: 'idea-50', client: client),
    ));

    await tester.tap(find.text('Закрыть'));
    await tester.pump();
    expect(client.calls, isEmpty);
    expect(find.text('Подтвердить закрытие'), findsOneWidget);

    await tester.tap(find.text('Подтвердить закрытие'));
    await tester.pumpAndSettle();

    expect(client.calls, hasLength(1));
    expect(client.calls.single.ideaId, 'idea-50');
    expect(client.calls.single.action, ManualTradeAction.close);
    expect(client.calls.single.quantity, isNull);
    expect(client.calls.single.stopPrice, isNull);
    expect(client.calls.single.idempotencyKey, isNotEmpty);
    expect(find.textContaining('Команда зафиксирована сервером'), findsOneWidget);
    expect(find.textContaining('Биржа ещё не подтвердила'), findsOneWidget);
  });

  testWidgets('SAI-050 reduce and tighten stop pass exact owner text only',
      (tester) async {
    final client = _FakeClient();
    await tester.pumpWidget(_host(
      ManualTradeControls(ideaId: 'idea-50', client: client),
    ));

    await tester.tap(find.text('Сократить'));
    await tester.pump();
    await tester.enterText(find.byType(TextField), '1.25');
    await tester.tap(find.text('Отправить'));
    await tester.pumpAndSettle();

    expect(client.calls.last.action, ManualTradeAction.reduce);
    expect(client.calls.last.quantity, '1.25');
    expect(client.calls.last.stopPrice, isNull);

    await tester.tap(find.text('Подтянуть стоп'));
    await tester.pump();
    await tester.enterText(find.byType(TextField), '89700');
    await tester.tap(find.text('Отправить'));
    await tester.pumpAndSettle();

    expect(client.calls.last.action, ManualTradeAction.tightenStop);
    expect(client.calls.last.stopPrice, '89700');
    expect(client.calls.last.quantity, isNull);
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
      ideaId: 'idea-50',
      planHash: 'plan-50',
      plannedQuantity: 4,
      state: state,
      protection: state.isProtected ? ProtectionStatus.placed : ProtectionStatus.none,
      fills: <Fill>[
        Fill(
          quantity: 4,
          price: 90000,
          at: DateTime.utc(2026, 8, 20, 14, 0),
        ),
      ],
      entryFilledAt: DateTime.utc(2026, 8, 20, 14, 0),
    );
