import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/api/sandbox_mirroring_engine_client.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';

class _ApproveApi extends ApiClient {
  _ApproveApi(this.ideaId)
      : super(baseUrl: 'https://engine.test', deviceToken: 'device-test');

  final String ideaId;

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    String? idempotencyKey,
    String? pairingSessionId,
  }) async =>
      {
        'idea_id': ideaId,
        'decision': 'APPROVED_PAPER',
        'idea_status': 'ACTIVE',
        'paper_only': true,
        'idempotent_replay': false,
        'trade': {
          'id': 'paper-1',
          'idea_id': ideaId,
          'symbol': 'SIU6',
          'direction': 'LONG',
          'status': 'OPEN',
          'entry': '90000',
          'initial_stop': '89500',
          'current_stop': '89500',
          'tp_prices': ['91000'],
          'tps_taken': 0,
          'realized_r': '0',
        },
      };
}

void main() {
  test('durability refusal emits one sandbox reconciliation diagnostic', () async {
    const ideaId = '11111111-1111-4111-8111-111111111111';
    final failures = <EngineHandledFailure>[];
    final results = <SandboxMirrorResult>[];
    final repository = LocalAnalysisRepository(store: LocalStore.inMemory());

    final client = SandboxMirroringEngineClient(
      repository: repository,
      instrumentStore: LocalStore.inMemory(),
      client: _ApproveApi(ideaId),
      onHandledFailure: failures.add,
      onResult: results.add,
    );

    final decision = await client.approvePaper(ideaId);
    await Future<void>.delayed(Duration.zero);

    expect(decision.decision, 'APPROVED_PAPER');
    expect(results, hasLength(1));
    expect(results.single.tone, SandboxMirrorTone.failure);
    expect(failures, hasLength(1));
    expect(failures.single.stage, EngineFailureStage.sandboxReconciliation);
    expect(
      failures.single.error.toString(),
      contains('не удалось надёжно записать состояние доставки'),
    );
  });
}
