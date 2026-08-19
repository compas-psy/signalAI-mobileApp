import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/state/risk_on_controller.dart';
import 'package:signalai/ui/widgets/risk_on_panel.dart';

class _RiskOnPanelApi extends ApiClient {
  _RiskOnPanelApi() : super(baseUrl: 'https://engine.test', deviceToken: '');

  int previewCalls = 0;
  int confirmCalls = 0;

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    if (path.endsWith('/risk-on/preview')) {
      previewCalls += 1;
      return {
        'idea_id': body?['idea_id'],
        'risk_snapshot_id': '11111111-1111-1111-1111-111111111111',
        'venue': body?['venue'],
        'account': body?['account'],
        'allowed': true,
        'blockers': <dynamic>[],
        'base_risk_pct': '0.005',
        'effective_risk_pct': '0.0075',
        'hard_cap_risk_pct': '0.0075',
        'base_quantity': '1',
        'effective_quantity': '2',
        'effective_risk_amount': '1400',
        'effective_leverage': null,
        'hard_cap_leverage': '3.0',
        'binding_limit': 'max_risk_per_trade',
        'preview_hash': 'b' * 64,
      };
    }
    if (path.endsWith('/risk-on/confirm')) {
      confirmCalls += 1;
      return {
        'risk_override_id': '22222222-2222-2222-2222-222222222222',
        'created': true,
        'preview_hash': body?['preview_hash'],
        'venue': body?['venue'],
        'account': body?['account'],
        'effective_risk_pct': '0.0075',
        'effective_quantity': '2',
        'effective_leverage': null,
        'hard_cap_risk_pct': '0.0075',
        'hard_cap_leverage': '3.0',
      };
    }
    throw StateError('unexpected POST $path');
  }
}

Widget _host(RiskOnController controller) => MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: RiskOnPanel(
            controller: controller,
            initialVenue: 'TINVEST',
            initialAccount: 'sandbox-main',
          ),
        ),
      ),
    );

void main() {
  testWidgets('Рискнуть is preview first and a second explicit confirmation writes',
      (tester) async {
    final api = _RiskOnPanelApi();
    final controller = RiskOnController(
      ideaId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      api: api,
    );

    await tester.pumpWidget(_host(controller));
    await tester.pumpAndSettle();

    expect(find.text('Рискнуть'), findsOneWidget);
    expect(find.textContaining('0,75%'), findsNothing);

    await tester.tap(find.text('Рискнуть'));
    await tester.pumpAndSettle();

    expect(api.previewCalls, 1);
    expect(api.confirmCalls, 0);
    expect(find.textContaining('0,50% → 0,75%'), findsOneWidget);
    expect(find.textContaining('1 → 2'), findsOneWidget);
    expect(find.textContaining('Плечо не увеличивается автоматически'), findsOneWidget);
    expect(find.text('Подтвердить Рискнуть'), findsOneWidget);

    await tester.tap(find.text('Подтвердить Рискнуть'));
    await tester.pumpAndSettle();

    expect(api.confirmCalls, 1);
    expect(find.textContaining('RISK ON подтверждён'), findsOneWidget);
  });

  testWidgets('server blocker is visible and confirm action is absent',
      (tester) async {
    final blockedApi = _BlockedRiskOnApi();
    final blockedController = RiskOnController(
      ideaId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      api: blockedApi,
    );

    await tester.pumpWidget(_host(blockedController));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Рискнуть'));
    await tester.pumpAndSettle();

    expect(find.textContaining('risk snapshot blocks new entries'), findsOneWidget);
    expect(find.text('Подтвердить Рискнуть'), findsNothing);
    expect(blockedApi.confirmCalls, 0);
  });
}

class _BlockedRiskOnApi extends _RiskOnPanelApi {
  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    if (path.endsWith('/risk-on/preview')) {
      previewCalls += 1;
      return {
        'idea_id': body?['idea_id'],
        'risk_snapshot_id': '11111111-1111-1111-1111-111111111111',
        'venue': body?['venue'],
        'account': body?['account'],
        'allowed': false,
        'blockers': ['risk snapshot blocks new entries'],
        'base_risk_pct': '0.005',
        'effective_risk_pct': '0.005',
        'hard_cap_risk_pct': '0.0075',
        'base_quantity': '1',
        'effective_quantity': '0',
        'effective_risk_amount': '0',
        'effective_leverage': null,
        'hard_cap_leverage': '3.0',
        'binding_limit': 'risk_snapshot',
        'preview_hash': '',
      };
    }
    return super.post(path, body: body, idempotencyKey: idempotencyKey);
  }
}
