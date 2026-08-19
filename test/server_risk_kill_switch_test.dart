import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/ui/screens/server_risk_screen.dart';

class _RiskApi extends ApiClient {
  _RiskApi() : super(baseUrl: 'https://engine.test', deviceToken: '');

  final List<({String path, Map<String, dynamic>? body})> posts = [];
  Map<String, dynamic> state = _dashboard(level: 'CLEAR', active: false);

  @override
  Future<Map<String, dynamic>> get(String path) async {
    expect(path, '/api/v1/risk/dashboard');
    return Map<String, dynamic>.from(state);
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    posts.add((path: path, body: body));
    if (path == '/api/v1/risk/resume') {
      state = _dashboard(level: 'CLEAR', active: false);
    } else {
      state = _dashboard(
        level: '${body?['level'] ?? 'HALT_NEW_ENTRIES'}',
        active: true,
      );
    }
    return Map<String, dynamic>.from(state);
  }
}

Map<String, dynamic> _dashboard({
  required String level,
  required bool active,
}) =>
    {
      'taken_at': '2026-08-19T04:30:00Z',
      'execution_mode': 'PAPER',
      'paper_only': true,
      'kill_switch': active,
      'kill_switch_level': level,
      'kill_switch_reason': active ? 'owner action' : '',
      'entries_blocked': false,
      'halted': false,
      'binding_limit': 'none',
      'current_drawdown': '0',
      'drawdown_multiplier': '1',
      'limits': <dynamic>[],
      'clusters': <String, dynamic>{},
      'has_data': false,
      'note': '',
    };

void main() {
  testWidgets('risk UI exposes the three distinct SAI-028 stop levels',
      (tester) async {
    final api = _RiskApi();
    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: ServerRiskScreen(api: api))),
    );
    await tester.pumpAndSettle();

    expect(find.text('Запретить новые входы'), findsOneWidget);
    expect(find.text('Отменить ожидающие входы'), findsOneWidget);
    expect(find.textContaining('FLATTEN_ALL'), findsWidgets);

    await tester.tap(find.text('Отменить ожидающие входы'));
    await tester.pumpAndSettle();

    expect(api.posts, hasLength(1));
    expect(api.posts.single.path, '/api/v1/risk/kill-switch');
    expect(api.posts.single.body?['level'], 'CANCEL_PENDING_ENTRIES');
    expect(api.posts.single.body?['confirm_flatten_all'], isFalse);
    expect(find.text('CANCEL'), findsOneWidget);
  });

  testWidgets('FLATTEN_ALL needs an explicit second confirmation in the UI',
      (tester) async {
    final api = _RiskApi();
    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: ServerRiskScreen(api: api))),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.textContaining('FLATTEN_ALL').first);
    await tester.pumpAndSettle();

    expect(api.posts, isEmpty);
    expect(find.text('Подтвердить FLATTEN_ALL'), findsOneWidget);

    await tester.tap(find.text('Подтвердить FLATTEN_ALL'));
    await tester.pumpAndSettle();

    expect(api.posts, hasLength(1));
    expect(api.posts.single.path, '/api/v1/risk/kill-switch');
    expect(api.posts.single.body?['level'], 'FLATTEN_ALL');
    expect(api.posts.single.body?['confirm_flatten_all'], isTrue);
    expect(find.text('FLATTEN'), findsOneWidget);
  });
}
