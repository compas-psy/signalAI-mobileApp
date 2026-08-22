import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/manual_trade_control_client.dart';

class _RecordingApi extends ApiClient {
  _RecordingApi() : super(baseUrl: 'https://engine.test', deviceToken: 'device');

  String? lastPath;
  Map<String, dynamic>? lastBody;
  String? lastIdempotencyKey;
  Map<String, dynamic> next = <String, dynamic>{};

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    String? pairingSessionId,
  }) async {
    lastPath = path;
    lastBody = body == null ? null : Map<String, dynamic>.from(body);
    lastIdempotencyKey = idempotencyKey;
    return Map<String, dynamic>.from(next);
  }
}

Map<String, dynamic> _resultJson({
  String action = 'CLOSE',
  bool reduceOnly = true,
  String status = 'REQUESTED',
}) => <String, dynamic>{
      'command_id': 'command-50',
      'intent_id': 'intent-50',
      'management_policy_snapshot_id': 'policy-49',
      'action': action,
      'status': status,
      'reduce_only': reduceOnly,
      'quantity': action == 'CLOSE' ? '4.000000000000' : null,
      'stop_price': null,
      'order_id': action == 'RETURN_AUTO' ? null : 'order-50',
      'order_status': action == 'RETURN_AUTO' ? null : 'REQUESTED',
      'created': true,
    };

void main() {
  test('SAI-050 CLOSE binds by idea and sends no client-authored economics', () async {
    final api = _RecordingApi()..next = _resultJson();
    final client = ManualTradeControlClient(api: api);

    final result = await client.request(
      ideaId: 'idea-50',
      action: ManualTradeAction.close,
      reason: 'owner close',
      idempotencyKey: 'tap-close-1',
    );

    expect(api.lastPath, '/api/v1/execution/ideas/idea-50/control');
    expect(api.lastBody, <String, dynamic>{
      'action': 'CLOSE',
      'reason': 'owner close',
    });
    for (final forbidden in <String>{
      'intent_id',
      'reduce_only',
      'side',
      'order_type',
      'venue',
      'account',
      'leverage',
    }) {
      expect(api.lastBody, isNot(contains(forbidden)), reason: forbidden);
    }
    expect(api.lastIdempotencyKey, 'tap-close-1');
    expect(result.action, ManualTradeAction.close);
    expect(result.status, 'REQUESTED');
    expect(result.reduceOnly, isTrue);
  });

  test('SAI-050 REDUCE sends only the owner requested reduction quantity', () async {
    final api = _RecordingApi()
      ..next = _resultJson(action: 'REDUCE')
        ..next['quantity'] = '1.25';
    final client = ManualTradeControlClient(api: api);

    await client.request(
      ideaId: 'idea-50',
      action: ManualTradeAction.reduce,
      reason: 'owner reduce',
      quantity: '1.25',
      idempotencyKey: 'tap-reduce-1',
    );

    expect(api.lastBody, <String, dynamic>{
      'action': 'REDUCE',
      'quantity': '1.25',
      'reason': 'owner reduce',
    });
  });

  test('SAI-050 TIGHTEN_STOP sends stop only and never a risk-widening flag', () async {
    final api = _RecordingApi()
      ..next = _resultJson(action: 'TIGHTEN_STOP')
        ..next['stop_price'] = '89700';
    final client = ManualTradeControlClient(api: api);

    await client.request(
      ideaId: 'idea-50',
      action: ManualTradeAction.tightenStop,
      reason: 'owner tighten',
      stopPrice: '89700',
      idempotencyKey: 'tap-stop-1',
    );

    expect(api.lastBody, <String, dynamic>{
      'action': 'TIGHTEN_STOP',
      'stop_price': '89700',
      'reason': 'owner tighten',
    });
    expect(api.lastBody, isNot(contains('widen_stop')));
  });

  test('SAI-050 client fails closed if server does not prove reduce-only', () async {
    final api = _RecordingApi()..next = _resultJson(reduceOnly: false);
    final client = ManualTradeControlClient(api: api);

    await expectLater(
      client.request(
        ideaId: 'idea-50',
        action: ManualTradeAction.close,
        reason: 'owner close',
        idempotencyKey: 'tap-close-unsafe',
      ),
      throwsA(isA<ApiException>()),
    );
  });
}
