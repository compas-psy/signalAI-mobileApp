import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/state/risk_on_controller.dart';

class _RiskOnApi extends ApiClient {
  _RiskOnApi() : super(baseUrl: 'https://engine.test', deviceToken: '');

  final List<({String path, Map<String, dynamic>? body, String? key})> posts = [];
  bool failNextConfirm = false;

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
  }) async {
    posts.add((path: path, body: body, key: idempotencyKey));
    if (path.endsWith('/risk-on/preview')) {
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
        'preview_hash': 'a' * 64,
      };
    }
    if (path.endsWith('/risk-on/confirm')) {
      if (failNextConfirm) {
        failNextConfirm = false;
        throw StateError('response lost');
      }
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

void main() {
  test('preview sends scope only; economics stay server-owned', () async {
    final api = _RiskOnApi();
    final controller = RiskOnController(
      ideaId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      api: api,
    );

    final preview = await controller.preview(
      venue: 'TINVEST',
      account: 'sandbox-main',
    );

    final request = api.posts.single;
    expect(request.path, '/api/v1/execution/risk-on/preview');
    expect(request.body, {
      'idea_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      'venue': 'TINVEST',
      'account': 'sandbox-main',
    });
    expect(request.body!.containsKey('effective_risk_pct'), isFalse);
    expect(request.body!.containsKey('effective_quantity'), isFalse);
    expect(request.body!.containsKey('effective_leverage'), isFalse);
    expect(preview.effectiveRiskPct, '0.0075');
    expect(preview.effectiveQuantity, '2');
    expect(preview.hardCapLeverage, '3.0');
  });

  test('confirm sends shown hash only and retries with the same idempotency key',
      () async {
    final api = _RiskOnApi();
    final controller = RiskOnController(
      ideaId: 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      api: api,
    );
    await controller.preview(venue: 'TINVEST', account: 'sandbox-main');

    api.failNextConfirm = true;
    await expectLater(controller.confirm(), throwsStateError);
    final failed = api.posts.last;
    expect(failed.path, '/api/v1/execution/risk-on/confirm');
    expect(failed.body, {
      'idea_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
      'venue': 'TINVEST',
      'account': 'sandbox-main',
      'preview_hash': 'a' * 64,
      'owner_confirmed': true,
    });
    expect(failed.body!.containsKey('effective_risk_pct'), isFalse);
    expect(failed.body!.containsKey('effective_quantity'), isFalse);
    expect(failed.body!.containsKey('effective_leverage'), isFalse);
    expect(failed.key, isNotEmpty);

    final result = await controller.confirm();
    final retried = api.posts.last;
    expect(retried.key, failed.key);
    expect(result.created, isTrue);
    expect(result.effectiveRiskPct, '0.0075');
    expect(result.effectiveQuantity, '2');
    expect(controller.previewData, isNull);
  });
}
