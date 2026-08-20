import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';

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
  }) async {
    lastPath = path;
    lastBody = body == null ? null : Map<String, dynamic>.from(body);
    lastIdempotencyKey = idempotencyKey;
    return Map<String, dynamic>.from(next);
  }
}

Map<String, dynamic> _previewJson({bool allowed = true}) => <String, dynamic>{
      'idea_id': 'idea-47',
      'risk_snapshot_id': 'risk-47',
      'preset_id': 'BOOST_1',
      'execution_mode': 'PAPER',
      'allowed': allowed,
      'warnings': <String>['headroom bounded'],
      'blockers': allowed ? <String>[] : <String>['RISK_STATE_BLOCKS_ENTRIES'],
      'auto_risk_pct': '0.005',
      'auto_risk_amount': '1000',
      'requested_risk_pct': '0.00625',
      'requested_risk_amount': '1250',
      'effective_risk_pct': '0.00625',
      'effective_risk_amount': '1250',
      'hard_cap_risk_pct': '0.0075',
      'quantity': '2',
      'notional': '180200',
      'resulting_leverage': null,
      'liquidation_distance_ratio': null,
      'total_open_risk_after': '0.01125',
      'cluster_risk_after': '0.008',
      'worst_case_stop_loss': '1250',
      'binding_constraint': 'preset_requested',
      'issued_at': '2026-08-20T11:00:00Z',
      'expires_at': '2026-08-20T11:05:00Z',
      'preview_hash': allowed ? 'v1.1787223900.deadbeefcafebabe' : '',
    };

void main() {
  test('SAI-047 preview request contains identity preset and observed mode only',
      () async {
    final api = _RecordingApi()..next = _previewJson();
    final engine = EngineClient(client: api);

    final preview = await engine.previewRisk(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );

    expect(api.lastPath, '/api/v1/risk/preview');
    expect(
      api.lastBody,
      <String, dynamic>{
        'idea_id': 'idea-47',
        'preset_id': 'BOOST_1',
        'current_mode': 'PAPER',
      },
    );
    expect(api.lastBody!.keys, hasLength(3));
    for (final forbidden in <String>{
      'risk_pct',
      'risk_amount',
      'quantity',
      'leverage',
      'notional',
      'liquidation_price',
    }) {
      expect(api.lastBody, isNot(contains(forbidden)), reason: forbidden);
    }
    expect(preview.allowed, isTrue);
    expect(preview.ideaId, 'idea-47');
    expect(preview.presetId, 'BOOST_1');
    expect(preview.executionMode, 'PAPER');
    expect(preview.effectiveRiskPct, '0.00625');
    expect(preview.quantity, '2');
    expect(preview.previewHash, 'v1.1787223900.deadbeefcafebabe');
    expect(preview.expiresAt.toUtc(), DateTime.utc(2026, 8, 20, 11, 5));
  });

  test('SAI-047 blocked preview stays visible but is never confirmable', () async {
    final api = _RecordingApi()..next = _previewJson(allowed: false);
    final engine = EngineClient(client: api);

    final preview = await engine.previewRisk(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );

    expect(preview.allowed, isFalse);
    expect(preview.canConfirm, isFalse);
    expect(preview.previewHash, isEmpty);
    expect(preview.blockers, contains('RISK_STATE_BLOCKS_ENTRIES'));
  });

  test('SAI-047 apply sends signed preview identity and no client economics',
      () async {
    final api = _RecordingApi()..next = _previewJson();
    final engine = EngineClient(client: api);
    final preview = await engine.previewRisk(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );
    final rawToken = preview.previewHash;

    api.next = <String, dynamic>{
      'override_id': 'override-47',
      'idea_id': 'idea-47',
      'risk_snapshot_id': 'risk-47',
      'preset_id': 'BOOST_1',
      'execution_mode': 'PAPER',
      'venue': 'MOEX',
      'account': 'paper-default',
      'effective_risk_pct': '0.00625',
      'effective_quantity': '2',
      'effective_leverage': null,
      'created': true,
    };

    final result = await engine.applyRisk(
      preview: preview,
      reason: 'owner confirmed reviewed preview',
    );

    expect(api.lastPath, '/api/v1/risk/override');
    expect(
      api.lastBody,
      <String, dynamic>{
        'idea_id': 'idea-47',
        'preset_id': 'BOOST_1',
        'current_mode': 'PAPER',
        'preview_hash': rawToken,
        'owner_confirmed': true,
        'reason': 'owner confirmed reviewed preview',
      },
    );
    for (final forbidden in <String>{
      'risk_pct',
      'risk_amount',
      'quantity',
      'leverage',
      'notional',
      'liquidation_price',
    }) {
      expect(api.lastBody, isNot(contains(forbidden)), reason: forbidden);
    }
    expect(api.lastIdempotencyKey, isNotNull);
    expect(api.lastIdempotencyKey, isNot(contains(rawToken)));
    expect(api.lastIdempotencyKey, contains('idea-47'));
    expect(result.created, isTrue);
    expect(result.overrideId, 'override-47');
    expect(result.effectiveRiskPct, '0.00625');
    expect(result.effectiveQuantity, '2');
  });

  test('SAI-047 parser rejects allowed preview without a signed token', () {
    final json = _previewJson()..['preview_hash'] = '';

    expect(() => RiskPreview.fromJson(json), throwsA(isA<ApiException>()));
  });
}
