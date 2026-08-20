import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/state/risk_boost_controller.dart';

RiskPreview _preview({
  String token = 'signed-preview-1',
  String risk = '0.00625',
  String quantity = '2',
  bool allowed = true,
  List<String> blockers = const [],
}) =>
    RiskPreview(
      ideaId: 'idea-47',
      riskSnapshotId: token == 'signed-preview-2' ? 'risk-48' : 'risk-47',
      presetId: 'BOOST_1',
      executionMode: 'PAPER',
      allowed: allowed,
      warnings: const ['server bounded'],
      blockers: blockers,
      autoRiskPct: '0.005',
      autoRiskAmount: '1000',
      requestedRiskPct: risk,
      requestedRiskAmount: '1250',
      effectiveRiskPct: risk,
      effectiveRiskAmount: '1250',
      hardCapRiskPct: '0.0075',
      quantity: quantity,
      notional: '180200',
      resultingLeverage: null,
      liquidationDistanceRatio: null,
      totalOpenRiskAfter: '0.01125',
      clusterRiskAfter: '0.008',
      worstCaseStopLoss: '1250',
      bindingConstraint: 'preset_requested',
      issuedAt: DateTime.utc(2026, 8, 20, token == 'signed-preview-2' ? 11 : 10),
      expiresAt: DateTime.utc(2099, 8, 20, 12),
      previewHash: allowed ? token : '',
    );

RiskOverrideResult _result(RiskPreview preview, {bool created = true}) =>
    RiskOverrideResult(
      overrideId: 'override-47',
      ideaId: preview.ideaId,
      riskSnapshotId: preview.riskSnapshotId,
      presetId: preview.presetId,
      executionMode: preview.executionMode,
      venue: 'MOEX',
      account: 'paper-default',
      effectiveRiskPct: preview.effectiveRiskPct,
      effectiveQuantity: preview.quantity,
      effectiveLeverage: preview.resultingLeverage,
      created: created,
    );

class _FakeEngine extends EngineClient {
  _FakeEngine()
      : super(
          client: ApiClient(
            baseUrl: 'https://engine.test',
            deviceToken: 'device',
          ),
        );

  final List<RiskPreview> previews = <RiskPreview>[];
  final List<RiskPreview> applied = <RiskPreview>[];
  Object? applyFailure;
  RiskOverrideResult? applyResult;
  int previewCalls = 0;

  @override
  Future<RiskPreview> previewRisk({
    required String ideaId,
    required String presetId,
    required String currentMode,
  }) async {
    previewCalls += 1;
    if (previews.isEmpty) throw StateError('no preview queued');
    return previews.removeAt(0);
  }

  @override
  Future<RiskOverrideResult> applyRisk({
    required RiskPreview preview,
    required String reason,
  }) async {
    applied.add(preview);
    final failure = applyFailure;
    applyFailure = null;
    if (failure != null) throw failure;
    return applyResult ?? _result(preview);
  }
}

void main() {
  test('SAI-047 controller stores server preview and never invents economics',
      () async {
    final engine = _FakeEngine()..previews.add(_preview());
    final controller = RiskBoostController(engine: engine);

    await controller.loadPreset(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );

    expect(controller.preview?.effectiveRiskPct, '0.00625');
    expect(controller.preview?.quantity, '2');
    expect(controller.canConfirm, isTrue);
    expect(controller.loading, isFalse);
    expect(controller.error, isNull);
    expect(controller.result, isNull);
    expect(engine.previewCalls, 1);
  });

  test('SAI-047 blocked server preview remains visible but cannot be applied',
      () async {
    final engine = _FakeEngine()
      ..previews.add(
        _preview(
          allowed: false,
          blockers: const ['RISK_STATE_BLOCKS_ENTRIES'],
        ),
      );
    final controller = RiskBoostController(engine: engine);

    await controller.loadPreset(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );

    expect(controller.preview?.blockers, contains('RISK_STATE_BLOCKS_ENTRIES'));
    expect(controller.canConfirm, isFalse);
    await expectLater(controller.confirm(), throwsStateError);
    expect(engine.applied, isEmpty);
  });

  test('SAI-047 stale 409 refreshes preview but never auto-applies new terms',
      () async {
    final first = _preview(token: 'signed-preview-1', risk: '0.00625');
    final refreshed = _preview(
      token: 'signed-preview-2',
      risk: '0.0055',
      quantity: '1',
    );
    final engine = _FakeEngine()
      ..previews.addAll([first, refreshed])
      ..applyFailure = ApiException(
        'signed preview is stale',
        statusCode: 409,
      );
    final controller = RiskBoostController(engine: engine);

    await controller.loadPreset(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );
    await controller.confirm();

    expect(engine.applied, hasLength(1));
    expect(engine.applied.single.previewHash, 'signed-preview-1');
    expect(engine.previewCalls, 2);
    expect(controller.result, isNull);
    expect(controller.preview?.previewHash, 'signed-preview-2');
    expect(controller.preview?.effectiveRiskPct, '0.0055');
    expect(controller.preview?.quantity, '1');
    expect(controller.needsReview, isTrue);
    expect(controller.canConfirm, isTrue);
    expect(controller.message, contains('изменились'));

    // Most important invariant: refreshing after 409 is display-only. The
    // second server preview cannot be applied until the owner taps again.
    expect(engine.applied, hasLength(1));

    engine.applyResult = _result(refreshed);
    await controller.confirm();
    expect(engine.applied, hasLength(2));
    expect(engine.applied.last.previewHash, 'signed-preview-2');
    expect(controller.result?.overrideId, 'override-47');
    expect(controller.preview, isNull);
    expect(controller.needsReview, isFalse);
    expect(controller.message, contains('Сделка не создана'));
  });

  test('SAI-047 unknown execution mode cannot request a risk preview', () async {
    final engine = _FakeEngine()..previews.add(_preview());
    final controller = RiskBoostController(engine: engine);

    await expectLater(
      controller.loadPreset(
        ideaId: 'idea-47',
        presetId: 'BOOST_1',
        currentMode: 'НЕИЗВЕСТЕН',
      ),
      throwsStateError,
    );
    expect(engine.previewCalls, 0);
    expect(controller.preview, isNull);
  });

  test('SAI-047 switching preset discards prior signed preview and result',
      () async {
    final engine = _FakeEngine()
      ..previews.addAll([
        _preview(token: 'signed-preview-1'),
        _preview(token: 'signed-preview-2'),
      ]);
    final controller = RiskBoostController(engine: engine);

    await controller.loadPreset(
      ideaId: 'idea-47',
      presetId: 'BOOST_1',
      currentMode: 'PAPER',
    );
    expect(controller.preview?.previewHash, 'signed-preview-1');

    await controller.loadPreset(
      ideaId: 'idea-47',
      presetId: 'BOOST_2',
      currentMode: 'PAPER',
    );

    expect(controller.preview?.previewHash, 'signed-preview-2');
    expect(controller.result, isNull);
    expect(controller.needsReview, isFalse);
  });
}
