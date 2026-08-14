import 'dart:collection';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/mock/demo_ideas.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/data/native_bridge.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/main.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/app_scope.dart';
import 'package:signalai/state/navigation.dart';
import 'package:signalai/ui/app_shell.dart';

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

class _ResumeEngine extends EngineClient {
  _ResumeEngine({required this.summary, required this.full});

  final Idea summary;
  final Idea full;
  final List<String> calls = <String>[];

  @override
  Future<EngineIdeas> today() async {
    calls.add('today');
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

Future<AppController> _pumpDemo(WidgetTester tester) async {
  tester.view.physicalSize = const Size(412, 892);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);

  await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
  await tester.pump(const Duration(milliseconds: 300));
  await tester.pump(const Duration(milliseconds: 400));

  final context = tester.element(find.byType(AppShell));
  return AppScope.read(context);
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('device acceptance stress contract', () {
    testWidgets('30 Settings ↔ Ideas route cycles keep shell responsive',
        (tester) async {
      final controller = await _pumpDemo(tester);

      for (var cycle = 0; cycle < 30; cycle++) {
        controller.goSection(AppSection.settings);
        controller.goPill(1);
        await tester.pump();
        expect(controller.section, AppSection.settings);
        expect(controller.pill, 1);
        expect(find.byType(AppShell), findsOneWidget);
        expect(tester.takeException(), isNull, reason: 'settings cycle $cycle');

        controller.goSection(AppSection.ideas);
        controller.goPill(cycle % AppSection.ideas.pills.length);
        await tester.pump();
        expect(controller.section, AppSection.ideas);
        expect(find.byType(AppShell), findsOneWidget);
        expect(tester.takeException(), isNull, reason: 'ideas cycle $cycle');
      }
    });

    testWidgets('same idea can be opened and backed out 30 times',
        (tester) async {
      final controller = await _pumpDemo(tester);

      for (var cycle = 0; cycle < 30; cycle++) {
        controller.goSection(AppSection.ideas);
        controller.openSignal('si');
        await tester.pump();
        expect(controller.isDetailOpen, isTrue);
        expect(controller.currentSignal?.id, 'si');
        expect(tester.takeException(), isNull, reason: 'open cycle $cycle');

        controller.back();
        await tester.pump();
        expect(controller.isDetailOpen, isFalse);
        expect(tester.takeException(), isNull, reason: 'back cycle $cycle');
      }
    });

    test('30 repeated launch payloads always reopen the requested idea', () async {
      final bridge = _QueuedLaunchBridge();
      final controller = AppController(
        DemoRepository(),
        bridge: bridge,
        prefs: LocalStore.inMemory(),
        thinMode: false,
      );
      addTearDown(controller.dispose);
      await controller.load();

      for (var cycle = 0; cycle < 30; cycle++) {
        bridge.enqueue('idea:si');
        await controller.openFromNotification();

        expect(controller.section, AppSection.ideas);
        expect(controller.isDetailOpen, isTrue);
        expect(controller.currentSignal?.id, 'si', reason: 'payload cycle $cycle');

        controller.back();
        expect(controller.isDetailOpen, isFalse);
      }
      expect(await bridge.takeLaunchPayload(), isEmpty);
    });

    test('resume refreshes thin feed before hydrating a deep-linked idea', () async {
      final now = DateTime.now();
      final full = DemoIdeas.all(now).first;
      final engine = _ResumeEngine(summary: _summaryOf(full), full: full);
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
      bridge.enqueue('idea:${full.id}');

      await controller.onAppResumed();
      await Future<void>.delayed(Duration.zero);

      expect(
        engine.calls.take(2).toList(),
        ['today', 'detail'],
        reason: 'fresh summary must arrive before full-card hydration',
      );
      expect(controller.currentIdea?.id, full.id);
      expect(controller.currentIdea?.plan, isNotNull);
    });

    test('malformed and empty launch payloads do not corrupt navigation', () async {
      final bridge = _QueuedLaunchBridge(['', 'idea:']);
      final controller = AppController(
        DemoRepository(),
        bridge: bridge,
        prefs: LocalStore.inMemory(),
        thinMode: false,
      );
      addTearDown(controller.dispose);
      await controller.load();
      final before = controller.route;

      await controller.openFromNotification();
      await controller.openFromNotification();

      expect(controller.route, before);
      expect(controller.isDetailOpen, isFalse);
    });
  });
}
