import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/state/risk_boost_controller.dart';
import 'package:signalai/ui/widgets/risk_boost_sheet.dart';

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
      issuedAt: DateTime.utc(2026, 8, 20, 10),
      expiresAt: DateTime.utc(2099, 8, 20, 12),
      previewHash: allowed ? token : '',
    );

RiskOverrideResult _result(RiskPreview preview) => RiskOverrideResult(
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
      created: true,
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

  @override
  Future<RiskPreview> previewRisk({
    required String ideaId,
    required String presetId,
    required String currentMode,
  }) async {
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
    return _result(preview);
  }
}

Widget _host(RiskBoostController controller) => Directionality(
      textDirection: TextDirection.ltr,
      child: SizedBox(
        width: 420,
        height: 800,
        child: RiskBoostSheet(
          controller: controller,
          ideaId: 'idea-47',
          symbol: 'SIU6',
          currentMode: 'PAPER',
          onClose: () {},
        ),
      ),
    );

void main() {
  testWidgets('SAI-047 sheet shows server economics and no-order disclaimer',
      (tester) async {
    final engine = _FakeEngine()..previews.add(_preview());
    final controller = RiskBoostController(engine: engine);

    await tester.pumpWidget(_host(controller));
    await tester.tap(find.text('BOOST 1'));
    await tester.pumpAndSettle();

    expect(find.text('Авто-риск'), findsOneWidget);
    expect(find.text('0,5%'), findsOneWidget);
    expect(find.text('После BOOST'), findsOneWidget);
    expect(find.text('0,625%'), findsOneWidget);
    expect(find.text('Количество'), findsOneWidget);
    expect(find.text('2'), findsOneWidget);
    expect(find.textContaining('Сделка не создаётся'), findsWidgets);
    expect(find.text('Зафиксировать риск'), findsOneWidget);
  });

  testWidgets('SAI-047 blocked preview cannot call apply', (tester) async {
    final engine = _FakeEngine()
      ..previews.add(
        _preview(
          allowed: false,
          blockers: const ['RISK_STATE_BLOCKS_ENTRIES'],
        ),
      );
    final controller = RiskBoostController(engine: engine);

    await tester.pumpWidget(_host(controller));
    await tester.tap(find.text('BOOST 1'));
    await tester.pumpAndSettle();

    expect(find.textContaining('RISK_STATE_BLOCKS_ENTRIES'), findsOneWidget);
    expect(find.text('Сервер не разрешает'), findsOneWidget);
    await tester.tap(find.text('Сервер не разрешает'));
    await tester.pump();
    expect(engine.applied, isEmpty);
  });

  testWidgets('SAI-047 stale refresh requires a second visible owner confirm',
      (tester) async {
    final first = _preview(token: 'signed-preview-1', risk: '0.00625');
    final refreshed = _preview(
      token: 'signed-preview-2',
      risk: '0.0055',
      quantity: '1',
    );
    final engine = _FakeEngine()
      ..previews.addAll([first, refreshed])
      ..applyFailure = ApiException('stale', statusCode: 409);
    final controller = RiskBoostController(engine: engine);

    await tester.pumpWidget(_host(controller));
    await tester.tap(find.text('BOOST 1'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Зафиксировать риск'));
    await tester.pumpAndSettle();

    expect(engine.applied, hasLength(1));
    expect(find.textContaining('Условия изменились'), findsOneWidget);
    expect(find.text('0,55%'), findsOneWidget);
    expect(find.text('1'), findsOneWidget);
    expect(find.text('Подтвердить новые условия'), findsOneWidget);

    // No automatic apply happened after the refresh.
    expect(engine.applied, hasLength(1));

    await tester.tap(find.text('Подтвердить новые условия'));
    await tester.pumpAndSettle();
    expect(engine.applied, hasLength(2));
    expect(find.textContaining('Сделка не создана'), findsOneWidget);
  });
}
