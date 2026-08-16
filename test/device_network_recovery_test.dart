import 'dart:collection';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/mock/demo_ideas.dart';
import 'package:signalai/data/native_bridge.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/app_lifecycle.dart';

import 'support/offline_http.dart';

class _QueuedLaunchBridge extends NativeBridge {
  _QueuedLaunchBridge([Iterable<String> payloads = const []])
      : _payloads = Queue<String>.of(payloads);

  final Queue<String> _payloads;

  void enqueue(String payload) => _payloads.add(payload);

  @override
  Future<String> takeLaunchPayload() async =>
      _payloads.isEmpty ? '' : _payloads.removeFirst();
}

class _RecoveringEngine extends EngineClient {
  _RecoveringEngine({required this.summary, required this.full});

  final Idea summary;
  final Idea full;
  bool offline = false;
  final List<String> calls = <String>[];

  @override
  Future<EngineIdeas> today() async {
    calls.add('today:${offline ? 'offline' : 'online'}');
    if (offline) {
      return const EngineIdeas.unavailable('временная ошибка сети');
    }
    return EngineIdeas(ideas: [summary]);
  }

  @override
  Future<Idea?> detail(String id) async {
    calls.add('detail');
    return id == full.id ? full : null;
  }
}

Idea _summaryOf(Idea full) => Idea(
      id: full.id,
      instrumentId: full.instrumentId,
      instrumentName: full.instrumentName,
      market: full.market,
      direction: full.direction,
      strategy: full.strategy,
      strategyVersion: full.strategyVersion,
      state: full.state,
      score: full.score,
      createdAt: full.createdAt,
      validUntil: full.validUntil,
      thesis: full.thesis,
      plan: null,
      timeframes: full.timeframes,
      readiness: full.readiness,
      actionable: full.actionable,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('foreground refresh recovers after a temporary network outage', () async {
    final full = DemoIdeas.all(DateTime.now()).first;
    final engine = _RecoveringEngine(summary: _summaryOf(full), full: full);
    final bridge = _QueuedLaunchBridge();
    final repository = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: LocalStore.inMemory(),
      vault: const SecureVault(),
    );
    final controller = AppController(
      repository,
      bridge: bridge,
      prefs: LocalStore.inMemory(),
      engine: engine,
      thinMode: true,
    );
    addTearDown(controller.dispose);

    await controller.load();
    engine.calls.clear();

    engine.offline = true;
    await resumeApp(
      controller: controller,
      repository: repository,
      thinMode: true,
    );
    expect(engine.calls, contains('today:offline'));

    engine.offline = false;
    bridge.enqueue('idea:${full.id}');
    await resumeApp(
      controller: controller,
      repository: repository,
      thinMode: true,
    );
    await Future<void>.delayed(Duration.zero);

    expect(
      engine.calls.where((call) => call == 'today:online'),
      isNotEmpty,
      reason: 'resume must retry the feed after connectivity returns',
    );
    expect(engine.calls.last, 'detail');
    expect(controller.currentIdea?.id, full.id);
    expect(controller.currentIdea?.plan, isNotNull);
  });
}
