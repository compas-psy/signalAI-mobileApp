import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';

import 'support/score_fixture.dart';
import 'package:signalai/state/app_controller.dart';

Idea sample({String id = 'idea_1'}) => Idea(
      id: id,
      instrumentId: 'SiZ6',
      instrumentName: 'Si-12.26',
      market: Market.forts,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'v1',
      state: IdeaState.triggered,
      score: scoreOf(80),
      createdAt: DateTime.utc(2026, 7, 29, 10),
      validUntil: DateTime.utc(2026, 7, 29, 18),
      thesis: '',
      plan: null,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('пропуск доезжает до журнала и переживает перезапуск', () async {
    // Журнал решений — не украшение: если запись теряется при перезапуске,
    // разрезы по причинам считаются по огрызку и врут.
    final store = LocalStore.inMemory();

    final first = LocalAnalysisRepository(store: store, vault: const SecureVault());
    final controller = AppController(first);
    addTearDown(controller.dispose);
    await controller.load();

    expect(controller.skipJournalAvailable, isTrue);
    expect(controller.skips, isEmpty);

    await controller.skipIdea(sample(),
        reason: SkipReason.riskLimit, comment: 'лимит на день выбран');

    expect(controller.skips.length, 1);
    expect(controller.skips.first.reason, SkipReason.riskLimit);
    expect(controller.skips.first.comment, 'лимит на день выбран');

    // Перезапуск: то же хранилище, новый репозиторий.
    final second =
        LocalAnalysisRepository(store: store, vault: const SecureVault());
    final restarted = AppController(second);
    addTearDown(restarted.dispose);
    await restarted.load();

    expect(restarted.skips.length, 1,
        reason: 'запись о решении обязана пережить перезапуск');
    expect(restarted.skips.first.instrumentId, 'SiZ6');
    expect(restarted.skips.first.reason, SkipReason.riskLimit);
  });

  test('пропуски копятся, новые сверху', () async {
    final store = LocalStore.inMemory();
    final controller = AppController(
      LocalAnalysisRepository(store: store, vault: const SecureVault()),
    );
    addTearDown(controller.dispose);
    await controller.load();

    await controller.skipIdea(sample(id: 'a'), reason: SkipReason.notSeen);
    await controller.skipIdea(sample(id: 'b'), reason: SkipReason.noTrust);

    expect(controller.skips.map((s) => s.ideaId).toList(), ['b', 'a']);
  });
}
