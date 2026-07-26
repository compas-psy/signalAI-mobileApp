import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/state_lock.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';
import 'package:signalai/domain/models/digest.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/monitor/background_cycle.dart';

/// Подставное приложение: журнал и выдача задаются тестом, а пересчёт только
/// отмечает, что его звали и с каким флагом.
class FakeTarget implements MonitorTarget {
  FakeTarget({
    SignalLedger? ledger,
    this.digest = const DailyDigest(
      title: '',
      subtitle: '',
      deliveryBadges: [],
      regime: [],
      regimeNote: '',
      events: [],
      signals: [],
      signalsQuota: '',
      sourceNote: '',
      rejections: [],
    ),
    this.positionsList = const [],
    this.nakedList = const [],
    this.stopAccepted = true,
    this.onFetch,
    this.failWith,
  }) : ledger = ledger ?? SignalLedger();

  @override
  final SignalLedger ledger;

  DailyDigest digest;
  List<BrokerPosition> positionsList;

  /// Позиции, которые брокер считает незащищёнными.
  List<BrokerPosition> nakedList;

  /// Примет ли брокер защитный стоп.
  bool stopAccepted;

  /// Запросы на постановку стопа: символ и уровень.
  final List<({String symbol, double price})> stopRequests = [];

  /// Что делать при пересчёте — например, «закрыть сделку».
  final void Function()? onFetch;

  /// Чем упасть, если проверяется устойчивость к отказу.
  final Object? failWith;

  final List<bool> forcedCalls = [];
  int positionCalls = 0;

  @override
  Future<DailyDigest> fetchDigest({bool force = false}) async {
    forcedCalls.add(force);
    if (failWith != null) throw failWith!;
    onFetch?.call();
    return digest;
  }

  @override
  Future<List<BrokerPosition>> brokerPositions() async {
    positionCalls++;
    return positionsList;
  }

  @override
  Future<List<BrokerPosition>> unprotectedPositions() async => nakedList;

  @override
  Future<bool> placeProtectiveStop({
    required String symbol,
    required double stopPrice,
    required bool long,
    required double quantity,
  }) async {
    stopRequests.add((symbol: symbol, price: stopPrice));
    return stopAccepted;
  }
}

TradingSignal signalOf(String symbol, {int score = 80, double entry = 100}) => TradingSignal(
      id: symbol,
      symbol: symbol,
      name: symbol,
      market: Market.crypto,
      direction: Direction.long,
      horizon: Horizon.swing,
      horizonLabel: '',
      score: score,
      entry: entry,
      stopLoss: entry - 2,
      takeProfits: const [],
      priceDecimals: 0,
      riskReward: '2,2',
      chips: const [],
      note: '',
      factors: const [],
      events: const [],
      unitRisk: 2,
      unitRiskLabel: '',
      unitMultiplier: 1,
      unitDecimals: 0,
      unitName: 'конт.',
      lastPrice: '100',
      changeLabel: '',
      changeUp: true,
      status: SignalStatus.proposed,
    );

PaperTrade tradeOf(
  String symbol, {
  required PaperStatus status,
  double? resultR,
  bool breakeven = false,
}) =>
    PaperTrade(
      id: symbol,
      symbol: symbol,
      strategyId: 'crypto',
      long: true,
      entry: 100,
      stopLoss: 98,
      tpPrices: const [104],
      tpShares: const [100],
      score: 80,
      createdAt: DateTime.utc(2026, 3, 10, 8),
      breakevenAfterTp1: breakeven,
      status: status,
      resultR: resultR,
      outcome: status == PaperStatus.closed ? 'TP1' : null,
      closedAt: status == PaperStatus.closed ? DateTime.utc(2026, 3, 10, 11) : null,
    );

void main() {
  final now = DateTime.utc(2026, 3, 10, 12, 5);

  StateLock freeLock() => StateLock(LocalStore.inMemory());

  Future<StateLock> lockHeldByUi() async {
    final lock = StateLock(LocalStore.inMemory());
    await lock.heartbeat(StateLock.ui);
    return lock;
  }

  test('передний план держит блокировку — фон не считает', () async {
    final target = FakeTarget();
    final cycle = BackgroundCycle(target: target, lock: await lockHeldByUi());

    final report = await cycle.run(state: MonitorState(), now: now);

    expect(report.skipped, isTrue);
    expect(report.notices, isEmpty);
    expect(target.forcedCalls, isEmpty, reason: 'пересчёт в это время делает интерфейс');
  });

  test('закрывшаяся сделка даёт уведомление один раз', () async {
    final ledger = SignalLedger(trades: [tradeOf('BTCUSDT', status: PaperStatus.open)]);
    final target = FakeTarget(
      ledger: ledger,
      // Пересчёт «прожил» сделку вперёд и закрыл её.
      onFetch: () {
        ledger.trades[0]
          ..status = PaperStatus.closed
          ..resultR = 1.4
          ..outcome = 'TP2';
      },
    );
    final cycle = BackgroundCycle(target: target, lock: freeLock());
    final state = MonitorState();

    final first = await cycle.run(state: state, now: now);
    expect(first.notices, hasLength(1));
    expect(first.notices.single.title, contains('BTCUSDT'));
    expect(first.notices.single.body, contains('+1,40R'));

    // Второй прогон: сделка всё ещё закрыта, но повторно будить нельзя.
    final second = await cycle.run(state: state, now: now.add(const Duration(hours: 1)));
    expect(second.notices, isEmpty);
  });

  test('сделка, закрытая до начала наблюдения, не будит', () async {
    // Журнал уже содержит закрытую сделку — это история, а не событие.
    final target = FakeTarget(
      ledger: SignalLedger(trades: [
        tradeOf('ETHUSDT', status: PaperStatus.closed, resultR: -1),
      ]),
    );
    final cycle = BackgroundCycle(target: target, lock: freeLock());

    final report = await cycle.run(state: MonitorState(), now: now);
    expect(report.notices, isEmpty);
  });

  test('сильная новая идея будит, слабая — нет', () async {
    final target = FakeTarget()
      ..digest = DailyDigest(
        title: '',
        subtitle: '',
        deliveryBadges: const [],
        regime: const [],
        regimeNote: '',
        events: const [],
        signals: [signalOf('SOLUSDT', score: 80), signalOf('XRPUSDT', score: 60)],
        signalsQuota: '',
        sourceNote: '',
        rejections: const [],
      );
    final cycle = BackgroundCycle(target: target, lock: freeLock());
    final state = MonitorState();

    final report = await cycle.run(state: state, now: now);
    expect(report.notices, hasLength(1));
    expect(report.notices.single.title, contains('SOLUSDT'));

    // Та же идея через час — не новость.
    expect((await cycle.run(state: state, now: now)).notices, isEmpty);
  });

  test('переставленный вход делает идею новой', () async {
    DailyDigest withEntry(double entry) => DailyDigest(
          title: '',
          subtitle: '',
          deliveryBadges: const [],
          regime: const [],
          regimeNote: '',
          events: const [],
          signals: [signalOf('SOLUSDT', entry: entry)],
          signalsQuota: '',
          sourceNote: '',
          rejections: const [],
        );

    final target = FakeTarget()..digest = withEntry(100);
    final cycle = BackgroundCycle(target: target, lock: freeLock());
    final state = MonitorState();

    await cycle.run(state: state, now: now);
    target.digest = withEntry(105);
    expect((await cycle.run(state: state, now: now)).notices, hasLength(1));
  });

  test('исчезнувшая позиция биржи даёт уведомление', () async {
    final target = FakeTarget(positionsList: const [
      BrokerPosition(
        symbol: 'BTCUSDT',
        long: true,
        quantity: 1,
        entryPrice: 100,
        unrealizedPnl: 0,
      ),
    ]);
    final cycle = BackgroundCycle(target: target, lock: freeLock());
    final state = MonitorState();

    // Первый прогон только запоминает, что позиция есть.
    expect((await cycle.run(state: state, now: now)).notices, isEmpty);

    target.positionsList = const [];
    final report = await cycle.run(state: state, now: now.add(const Duration(hours: 1)));
    expect(report.notices, hasLength(1));
    expect(report.notices.single.title, contains('BTCUSDT'));
  });

  test('пересчёт форсируется только на новом часовом баре', () async {
    final target = FakeTarget();
    final cycle = BackgroundCycle(target: target, lock: freeLock());
    final state = MonitorState();

    await cycle.run(state: state, now: DateTime.utc(2026, 3, 10, 12, 5));
    expect(target.forcedCalls.last, isTrue, reason: 'первый прогон — считаем');

    await cycle.run(state: state, now: DateTime.utc(2026, 3, 10, 12, 40));
    expect(target.forcedCalls.last, isFalse, reason: 'тот же час — хватит кэша');

    await cycle.run(state: state, now: DateTime.utc(2026, 3, 10, 13, 2));
    expect(target.forcedCalls.last, isTrue, reason: 'бар закрылся — пересчёт');
  });

  test('отказ сети не роняет цикл и виден в отчёте', () async {
    final target = FakeTarget(failWith: Exception('нет связи'));
    final cycle = BackgroundCycle(target: target, lock: freeLock());
    final state = MonitorState();

    final report = await cycle.run(state: state, now: now);

    expect(report.skipped, isFalse);
    expect(report.error, contains('нет связи'));
    expect(report.summary, contains('не удался'));
    expect(state.lastRun, now, reason: 'прогон был — следующий пойдёт по расписанию');
  });

  test('блокировка отпускается даже после отказа', () async {
    final lock = freeLock();
    final cycle = BackgroundCycle(
      target: FakeTarget(failWith: Exception('нет связи')),
      lock: lock,
    );

    await cycle.run(state: MonitorState(), now: now);
    expect(await lock.heldByOther(StateLock.ui), isFalse);
  });

  group('Защита позиции', () {
    BrokerPosition naked(String symbol) => BrokerPosition(
          symbol: symbol,
          long: true,
          quantity: 2,
          entryPrice: 100,
          unrealizedPnl: 0,
        );

    test('голая позиция получает стоп по уровню из журнала', () async {
      final target = FakeTarget(
        ledger: SignalLedger(trades: [tradeOf('BTCUSDT', status: PaperStatus.open)]),
        nakedList: [naked('BTCUSDT')],
      );
      final cycle = BackgroundCycle(target: target, lock: freeLock());

      final report = await cycle.run(state: MonitorState(), now: now);

      expect(target.stopRequests, hasLength(1));
      // Уровень решила стратегия при выдаче идеи — выдумывать нельзя.
      expect(target.stopRequests.single.price, 98);
      expect(report.notices.map((n) => n.title), contains(contains('защита восстановлена')));
    });

    test('после TP1 защита восстанавливается на безубытке, не на стопе', () async {
      final trade = tradeOf('BTCUSDT', status: PaperStatus.open, breakeven: true)
        ..tpsTaken = 1;
      final target = FakeTarget(
        ledger: SignalLedger(trades: [trade]),
        nakedList: [naked('BTCUSDT')],
      );
      final cycle = BackgroundCycle(target: target, lock: freeLock());

      await cycle.run(state: MonitorState(), now: now);

      // Исходный стоп 98 после TP1 означал бы снова рисковать заработанным.
      expect(target.stopRequests.single.price, 100);
    });

    test('защищённая позиция ничего не трогает', () async {
      final target = FakeTarget(
        ledger: SignalLedger(trades: [tradeOf('BTCUSDT', status: PaperStatus.open)]),
      );
      final cycle = BackgroundCycle(target: target, lock: freeLock());

      final report = await cycle.run(state: MonitorState(), now: now);
      expect(target.stopRequests, isEmpty);
      expect(report.notices, isEmpty);
    });

    test('чужой позиции стоп не навязывается', () async {
      // Журнал про этот символ ничего не знает: позицию открыли руками.
      final target = FakeTarget(nakedList: [naked('GAZP')]);
      final cycle = BackgroundCycle(target: target, lock: freeLock());

      final report = await cycle.run(state: MonitorState(), now: now);

      expect(target.stopRequests, isEmpty,
          reason: 'ставить чужой позиции свой уровень — не наше дело');
      expect(report.notices.single.body, contains('не открывало'));
    });

    test('про чужую позицию сообщается один раз, а не каждый час', () async {
      final target = FakeTarget(nakedList: [naked('GAZP')]);
      final cycle = BackgroundCycle(target: target, lock: freeLock());
      final state = MonitorState();

      expect((await cycle.run(state: state, now: now)).notices, hasLength(1));
      expect((await cycle.run(state: state, now: now)).notices, isEmpty);
    });

    test('первая неудача молчит, вторая будит', () async {
      final target = FakeTarget(
        ledger: SignalLedger(trades: [tradeOf('BTCUSDT', status: PaperStatus.open)]),
        nakedList: [naked('BTCUSDT')],
        stopAccepted: false,
      );
      final cycle = BackgroundCycle(target: target, lock: freeLock());
      final state = MonitorState();

      final first = await cycle.run(state: state, now: now);
      expect(first.notices, isEmpty, reason: 'один сбой сети — не повод будить ночью');
      expect(state.stopFailures['BTCUSDT'], 1);

      final second = await cycle.run(state: state, now: now.add(const Duration(hours: 1)));
      expect(second.notices, hasLength(1));
      expect(second.notices.single.body, contains('не удалось'));
    });

    test('удачная постановка сбрасывает счётчик неудач', () async {
      final target = FakeTarget(
        ledger: SignalLedger(trades: [tradeOf('BTCUSDT', status: PaperStatus.open)]),
        nakedList: [naked('BTCUSDT')],
        stopAccepted: false,
      );
      final cycle = BackgroundCycle(target: target, lock: freeLock());
      final state = MonitorState();

      await cycle.run(state: state, now: now);
      target.stopAccepted = true;
      await cycle.run(state: state, now: now.add(const Duration(hours: 1)));

      expect(state.stopFailures.containsKey('BTCUSDT'), isFalse);
    });

    test('счётчик неудач переживает перезапуск', () {
      final state = MonitorState()..stopFailures['BTCUSDT'] = 1;
      final restored = MonitorState.fromJson(state.toJson());
      expect(restored.stopFailures['BTCUSDT'], 1,
          reason: 'иначе после перезапуска тревога никогда не сработает');
    });
  });

  test('состояние переживает сериализацию', () {
    final state = MonitorState(
      lastRun: now,
      notifiedTrades: {'a'},
      notifiedSignals: {'b'},
      knownPositions: {'BTCUSDT'},
      lastError: 'таймаут',
    );

    final restored = MonitorState.fromJson(state.toJson());
    expect(restored.lastRun, now);
    expect(restored.notifiedTrades, {'a'});
    expect(restored.notifiedSignals, {'b'});
    expect(restored.knownPositions, {'BTCUSDT'});
    expect(restored.lastError, 'таймаут');
  });
}
