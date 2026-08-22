import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/state/execution_mode_controller.dart';

class _ModeApi extends ApiClient {
  _ModeApi() : super(baseUrl: 'https://engine.test', deviceToken: '');

  final List<String> gets = [];
  final List<({String path, Map<String, dynamic>? body, String? key})> posts = [];
  String mode = 'CANARY';
  Map<String, dynamic> modePreview = {
    'current': 'CANARY',
    'target': 'SANDBOX',
    'allowed': true,
    'blockers': <dynamic>[],
  };
  Map<String, dynamic> livePreview = {
    'preview_hash': 'a' * 64,
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
    'config_hash': 'b' * 64,
    'allowed': false,
    'blockers': [
      'explicit owner confirmation missing',
      'risk.paper_only=true',
      'execution venue/account not configured',
    ],
  };

  @override
  Future<Map<String, dynamic>> get(String path) async {
    gets.add(path);
    if (path == '/api/v1/execution/mode') {
      return {'mode': mode, 'updated_at': '2026-08-19T08:00:00Z'};
    }
    throw StateError('unexpected GET $path');
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    String? pairingSessionId,
  }) async {
    posts.add((path: path, body: body, key: idempotencyKey));
    if (path == '/api/v1/execution/mode/preview') {
      return Map<String, dynamic>.from(modePreview);
    }
    if (path == '/api/v1/execution/mode/change') {
      mode = '${body?['target']}';
      return {'mode': mode, 'updated_at': '2026-08-19T08:01:00Z'};
    }
    if (path == '/api/v1/execution/live/preview') {
      return Map<String, dynamic>.from(livePreview);
    }
    if (path == '/api/v1/execution/live/confirm') {
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
  test('load reads the server-owned lifecycle mode', () async {
    final api = _ModeApi();
    final controller = ExecutionModeController(api: api);

    await controller.load();

    expect(controller.mode, ServerExecutionMode.canary);
    expect(controller.modeKnown, isTrue);
    expect(api.gets, ['/api/v1/execution/mode']);
  });

  test('blocked preview never changes mode', () async {
    final api = _ModeApi()
      ..modePreview = {
        'current': 'CANARY',
        'target': 'LIVE',
        'allowed': false,
        'blockers': ['two-step owner activation required'],
      };
    final controller = ExecutionModeController(api: api);
    await controller.load();

    final preview = await controller.previewMode(ServerExecutionMode.live);

    expect(preview.allowed, isFalse);
    expect(preview.blockers, contains('two-step owner activation required'));
    expect(
      api.posts.where((item) => item.path == '/api/v1/execution/mode/change'),
      isEmpty,
    );
    expect(controller.mode, ServerExecutionMode.canary);
  });

  test('allowed generic transition still needs explicit confirm', () async {
    final api = _ModeApi();
    final controller = ExecutionModeController(api: api);
    await controller.load();

    final preview = await controller.previewMode(ServerExecutionMode.sandbox);
    expect(preview.allowed, isTrue);
    expect(controller.mode, ServerExecutionMode.canary);
    expect(api.posts.where((item) => item.path.endsWith('/change')), isEmpty);

    await controller.confirmModeChange(reason: 'owner confirmed downshift');

    expect(controller.mode, ServerExecutionMode.sandbox);
    expect(
      api.posts.where((item) => item.path == '/api/v1/execution/mode/change'),
      hasLength(1),
    );
  });

  test('blocked LIVE preview exposes exact context and cannot confirm', () async {
    final api = _ModeApi();
    final controller = ExecutionModeController(api: api);
    await controller.load();

    final preview = await controller.previewLive();

    expect(preview.venue, 'NOT_CONFIGURED');
    expect(preview.account, 'NOT_CONFIGURED');
    expect(preview.capitalRub, '300000.00000000');
    expect(preview.hardCaps['max_leverage'], '3.0');
    expect(preview.confirmable, isFalse);
    expect(
      () => controller.confirmLive(idempotencyKey: 'live-confirm-1'),
      throwsStateError,
    );
    expect(api.posts.where((item) => item.path.endsWith('/live/confirm')), isEmpty);
  });

  test('LIVE confirm uses preview hash, explicit owner flag and idempotency key',
      () async {
    final api = _ModeApi()
      ..livePreview = {
        'preview_hash': 'c' * 64,
        'from_mode': 'CANARY',
        'target_mode': 'LIVE',
        'venue': 'LIGHTER',
        'account': 'canary-main',
        'capital_rub': '10000.00000000',
        'hard_caps': {'max_leverage': '2.0'},
        'config_hash': 'd' * 64,
        'allowed': false,
        'blockers': ['explicit owner confirmation missing'],
      };
    final controller = ExecutionModeController(api: api);
    await controller.load();

    final preview = await controller.previewLive();
    expect(preview.confirmable, isTrue);
    await controller.confirmLive(idempotencyKey: 'live-confirm-1');

    final confirm = api.posts.singleWhere(
      (item) => item.path == '/api/v1/execution/live/confirm',
    );
    expect(confirm.body?['preview_hash'], 'c' * 64);
    expect(confirm.body?['owner_confirmed'], isTrue);
    expect(confirm.key, 'live-confirm-1');
    expect(controller.mode, ServerExecutionMode.live);
  });
}
