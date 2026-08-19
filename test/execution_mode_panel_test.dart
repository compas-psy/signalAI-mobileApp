import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/state/execution_mode_controller.dart';
import 'package:signalai/ui/widgets/execution_mode_panel.dart';

class _PanelApi extends ApiClient {
  _PanelApi() : super(baseUrl: 'https://engine.test', deviceToken: '');

  String mode = 'CANARY';
  Map<String, dynamic> modePreview = {
    'current': 'CANARY',
    'target': 'SANDBOX',
    'allowed': true,
    'blockers': <dynamic>[],
  };
  Map<String, dynamic> livePreview = {
    'preview_hash': 'e' * 64,
    'from_mode': 'CANARY',
    'target_mode': 'LIVE',
    'venue': 'NOT_CONFIGURED',
    'account': 'NOT_CONFIGURED',
    'capital_rub': '300000.00000000',
    'hard_caps': {
      'max_risk_per_trade': '0.0075',
      'max_total_open_risk': '0.02',
      'daily_loss_limit': '0.015',
      'max_leverage': '3.0',
    },
    'config_hash': 'f' * 64,
    'allowed': false,
    'blockers': [
      'explicit owner confirmation missing',
      'risk.paper_only=true',
      'execution venue/account not configured',
    ],
  };
  final List<({String path, Map<String, dynamic>? body, String? key})> posts = [];

  @override
  Future<Map<String, dynamic>> get(String path) async =>
      {'mode': mode, 'updated_at': '2026-08-19T08:00:00Z'};

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    posts.add((path: path, body: body, key: idempotencyKey));
    if (path.endsWith('/mode/preview')) return modePreview;
    if (path.endsWith('/mode/change')) {
      mode = '${body?['target']}';
      return {'mode': mode, 'updated_at': '2026-08-19T08:01:00Z'};
    }
    if (path.endsWith('/live/preview')) return livePreview;
    if (path.endsWith('/live/confirm')) {
      mode = 'LIVE';
      return {
        'preview_hash': body?['preview_hash'],
        'idempotency_key': idempotencyKey,
        'status': 'APPLIED',
        'mode': 'LIVE',
        'blockers': <dynamic>[],
      };
    }
    throw StateError('unexpected POST $path');
  }
}

void main() {
  testWidgets('allowed downshift needs a visible second confirmation',
      (tester) async {
    final api = _PanelApi();
    final controller = ExecutionModeController(api: api);
    await controller.load();

    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: ExecutionModePanel(controller: controller))),
    );
    await tester.pumpAndSettle();

    expect(find.textContaining('Текущий режим · CANARY'), findsOneWidget);
    await tester.tap(find.text('SANDBOX'));
    await tester.pumpAndSettle();

    expect(find.text('Подтвердить SANDBOX'), findsOneWidget);
    expect(api.posts.where((item) => item.path.endsWith('/mode/change')), isEmpty);

    await tester.tap(find.text('Подтвердить SANDBOX'));
    await tester.pumpAndSettle();

    expect(controller.mode, ServerExecutionMode.sandbox);
    expect(api.posts.where((item) => item.path.endsWith('/mode/change')), hasLength(1));
  });

  testWidgets('blocked promotion shows server blockers and does not write',
      (tester) async {
    final api = _PanelApi()
      ..modePreview = {
        'current': 'CANARY',
        'target': 'LIVE',
        'allowed': false,
        'blockers': ['two-step owner activation required'],
      };
    final controller = ExecutionModeController(api: api);
    await controller.load();

    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: ExecutionModePanel(controller: controller))),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('SANDBOX'));
    await tester.pumpAndSettle();

    expect(find.textContaining('two-step owner activation required'), findsOneWidget);
    expect(find.textContaining('Подтвердить'), findsNothing);
    expect(api.posts.where((item) => item.path.endsWith('/mode/change')), isEmpty);
  });

  testWidgets('LIVE preview shows exact context and remains non-confirmable when blocked',
      (tester) async {
    final api = _PanelApi();
    final controller = ExecutionModeController(api: api);
    await controller.load();

    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: ExecutionModePanel(controller: controller))),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('LIVE'));
    await tester.pumpAndSettle();

    expect(find.textContaining('NOT_CONFIGURED'), findsWidgets);
    expect(find.textContaining('300 000'), findsOneWidget);
    expect(find.textContaining('max_leverage · 3.0'), findsOneWidget);
    expect(find.textContaining('risk.paper_only=true'), findsOneWidget);
    expect(find.text('Подтвердить LIVE'), findsNothing);
    expect(api.posts.where((item) => item.path.endsWith('/live/confirm')), isEmpty);
  });
}
