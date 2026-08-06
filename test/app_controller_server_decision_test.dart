import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/market/idea_chart_source.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/data/native_bridge.dart';
import 'package:signalai/data/repository.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/paper_position.dart';
import 'package:signalai/domain/models/digest.dart';
import 'package:signalai/domain/models/portfolio.dart';
import 'package:signalai/domain/models/settings.dart';
import 'package:signalai/domain/models/strategy.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/state/app_controller.dart';

/// Не DemoRepository по типу, чтобы контроллер шёл в серверную ветку, но
/// готовые вспомогательные ответы берём у стабильной демо-фикстуры.
class _Repository implements SignalAiRepository {
  final DemoRepository _delegate = DemoRepository();
  int localConfirmations = 0;
  int digestFetches = 0;

  @override
  Future<DailyDigest> fetchDigest({bool force = false}) {
    digestFetches++;
    return _delegate.fetchDigest(force: force);
  }

  @override
  Future<TradesSummary> fetchTrades() => _delegate.fetchTrades();

  @override
  Future<StrategiesSnapshot> fetchStrategies() => _delegate.fetchStrategies();

  @override
  Future<SettingsSnapshot> fetchSettings() => _delegate.fetchSettings();

  @override
  Future<void> confirmSignal(String signalId) async {
    localConfirmations++;
  }

  @override
  Future<void> setStrategyEnabled(String strategyId, bool enabled) =>
      _delegate.setStrategyEnabled(strategyId, enabled);

  @override
  Future<BacktestResult> runBacktest(String strategyId) =>
      _delegate.runBacktest(strategyId);

  @override
  Future<ExchangeAccount> connectExchange(String exchangeId) =>
      _delegate.connectExchange(exchangeId);

  @override
  Future<void> setChannelEnabled(String channelId, bool enabled) =>
      _delegate.setChannelEnabled(channelId, enabled);

  @override
  Future<void> setNotificationEnabled(String notificationId, bool enabled) =>
      _delegate.setNotificationEnabled(notificationId, enabled);

  @override
  Future<RiskProfile> updateRiskProfile({double? deposit, double? riskPercent}) =>
      _delegate.updateRiskProfile(
        deposit: deposit,
        riskPercent: riskPercent,
      );
}

class _NoDeviceAuth extends NativeBridge {
  const _NoDeviceAuth();

  @override
  Future<String?> confirmMethod() async => 'none';
}

class _Engine extends EngineClient {
  _Engine(this.idea, {Idea? detailIdea}) : _detailIdea = detailIdea ?? idea;

  final Idea idea;
  final Idea _detailIdea;
  int approvals = 0;
  int barCalls = 0;
  int todayCalls = 0;

  final PaperPosition trade = const PaperPosition(
    id: 'paper-1',
    ideaId: 'active',
    symbol: 'BTCUSDT',
    long: true,
    pending: true,
    entry: 60050,
    initialStop: 59000,
    currentStop: 59000,
    tpPrices: [61000, 62000, 63000],
    tpsTaken: 0,
    fromServer: true,
  );

  @override
  Future<EngineIdeas> today() async {
    todayCalls++;
    return EngineIdeas(ideas: [idea]);
  }

  @override
  Future<Idea?> detail(String id) async => _detailIdea;

  @override
  Future<Map<String, dynamic>?> dataStatus() async => const {};

  @override
  Future<List<PaperPosition>?> paperTrades({bool liveOnly = true}) async =>
      approvals == 0 ? const [] : [trade];

  @override
  Future<IdeaDecision> approvePaper(String ideaId) async {
    approvals++;
    return IdeaDecision(
      ideaId: ideaId,
      decision: 'APPROVED_PAPER',
      ideaStatus: 'ACTIVE',
      paperOnly: true,
      idempotentReplay: false,
      trade: trade,
    );
  }

  @override
  Future<SignalChart?> barsWithFallback(
    String instrumentId, {
    required String setupTimeframe,
    int limit = 200,
    void Function(String reason)? onFailure,
  }) async {
    barCalls++;
    onFailure?.call('сервер не отдал свечи после fallback');
    return null;
  }
}

class _ChartSpy extends IdeaChartSource {
  int calls = 0;

  @override
  Future<SignalChart?> load(
    Idea idea, {
    String timeframe = '',
    void Function(String reason)? onFailure,
  }) async {
    calls++;
    return null;
  }
}

class _UnsafeDecisionEngine extends _Engine {
  _UnsafeDecisionEngine(super.idea);

  @override
  Future<IdeaDecision> approvePaper(String ideaId) async {
    approvals++;
    throw ApiException(
      'Сервер не подтвердил безопасный режим paper-only. Решение не принято.',
    );
  }
}

Map<String, dynamic> _detail() => {
      'id': 'active',
      'instrument_id': 'CRYPTO:PERP:BTCUSDT',
      'symbol': 'BTCUSDT',
      'strategy': 'TREND_PULLBACK',
      'direction': 'LONG',
      'status': 'TRIGGERED',
      'quality_status': 'ACTIVE',
      'score': '82',
      'signal_time': '2026-08-06T09:00:00Z',
      'expires_at': '2026-08-07T09:00:00Z',
      'context_timeframe': '1d',
      'setup_timeframe': '4h',
      'trigger_timeframe': '1h',
      'plan': {
        'order_intent': 'LIMIT_RETEST',
        'entry_low': '60000',
        'entry_high': '60100',
        'stop': '59000',
        'tp1': '61000',
        'tp2': '62000',
        'tp3': '63000',
      },
      'sizing': {
        'risk_pct': '0.0075',
        'risk_amount': '1000',
        'quantity': '0.01',
        'risk_per_unit': '1000',
        'tradable': true,
      },
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('серверная идея создаёт server paper trade, а не локальную сделку',
      () async {
    final repository = _Repository();
    final idea = EngineContract.idea(_detail());
    final engine = _Engine(idea);
    final controller = AppController(
      repository,
      engine: engine,
      bridge: const _NoDeviceAuth(),
      prefs: LocalStore.inMemory(),
    );
    addTearDown(controller.dispose);

    await controller.load();
    controller.openSignal(idea.id);

    // Программный вызов без явного sheet не является решением владельца.
    await controller.confirmCurrentSignal();
    expect(engine.approvals, 0);

    controller.openSheet();
    await controller.confirmCurrentSignal();

    expect(engine.approvals, 1);
    expect(repository.localConfirmations, 0);
    expect(controller.paperPositions.map((trade) => trade.id), ['paper-1']);
    expect(controller.currentIdea?.state.name, 'active');
  });

  test('небезопасный approve не обновляет идею и paper ledger', () async {
    final repository = _Repository();
    final idea = EngineContract.idea(_detail());
    final engine = _UnsafeDecisionEngine(idea);
    final controller = AppController(
      repository,
      engine: engine,
      bridge: const _NoDeviceAuth(),
      prefs: LocalStore.inMemory(),
    );
    addTearDown(controller.dispose);

    await controller.load();
    controller.openSignal(idea.id);
    controller.openSheet();
    final before = controller.currentIdea?.state;

    await controller.confirmCurrentSignal();

    expect(engine.approvals, 1);
    expect(repository.localConfirmations, 0);
    expect(controller.paperPositions, isEmpty);
    expect(controller.currentIdea?.state, before);
    expect(controller.toast, contains('paper-only'));
  });

  test('догрузка detail не повышает wait_for_trigger до actionable',
      () async {
    final full = EngineContract.idea(_detail());
    final waiting = full.copyWith(
      readiness: IdeaReadiness.waiting,
      actionable: false,
    );
    final engine = _Engine(waiting, detailIdea: full);
    final controller = AppController(
      _Repository(),
      engine: engine,
      bridge: const _NoDeviceAuth(),
      prefs: LocalStore.inMemory(),
    );
    addTearDown(controller.dispose);

    await controller.load();
    controller.openSignal(waiting.id);
    await Future<void>.delayed(Duration.zero);

    expect(controller.currentIdea?.readiness, IdeaReadiness.waiting);
    expect(controller.currentIdea?.actionable, isFalse);
    expect(controller.currentIdea?.canApprovePaper, isFalse);
    controller.openSheet();
    await controller.confirmCurrentSignal();
    expect(engine.approvals, 0);
  });

  test('production chart не вызывает прямой market source', () async {
    final idea = EngineContract.idea(_detail());
    final engine = _Engine(idea);
    final direct = _ChartSpy();
    final controller = AppController(
      _Repository(),
      engine: engine,
      chartSource: direct,
      bridge: const _NoDeviceAuth(),
      prefs: LocalStore.inMemory(),
    );
    addTearDown(controller.dispose);

    await controller.load();
    await controller.loadIdeaChart(idea);

    expect(engine.barCalls, 1);
    expect(direct.calls, 0);
    expect(controller.ideaChartFailed(idea), isTrue);
    expect(
      controller.ideaChartFailureReason(idea),
      contains('сервер не отдал свечи'),
    );
  });

  test('cold start thin спрашивает engine и не запускает local analyzer',
      () async {
    final repository = _Repository();
    final engine = _Engine(EngineContract.idea(_detail()));
    final controller = AppController(
      repository,
      engine: engine,
      thinMode: true,
      bridge: const _NoDeviceAuth(),
      prefs: LocalStore.inMemory(),
    );
    addTearDown(controller.dispose);

    await controller.load();

    expect(engine.todayCalls, 1);
    expect(repository.digestFetches, 0);
    expect(repository.localConfirmations, 0);
    expect(controller.digest, isNull);
    expect(controller.ideas, isNotEmpty);
    expect(controller.paperPositions.every((trade) => trade.fromServer), isTrue);
  });
}
