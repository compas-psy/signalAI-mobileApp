import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';
import 'package:signalai/domain/models/settings.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/domain/risk/risk_engine.dart';

/// Риск-правила должны быть исполняемыми, а не декларативными: каждый гейт
/// здесь либо реально запрещает сделку, либо его нет.
void main() {
  final now = DateTime.utc(2026, 3, 10, 12);
  const risk = RiskProfile(
    deposit: 100000,
    riskPercent: 1,
    dailyLossLimit: '',
    maxConcurrentTrades: '',
    pauseRule: '',
  );

  InstrumentSpec specOf(String symbol, {Market market = Market.forts}) => InstrumentSpec(
        id: symbol.toLowerCase(),
        symbol: symbol,
        name: symbol,
        market: market,
        priceDecimals: 0,
        valuePerPoint: 1,
        unitMultiplier: 1,
        unitDecimals: 0,
        unitName: 'конт.',
        unitRiskSuffix: 'контракт',
        tickSize: 1,
      );

  TradingSignal signalOf(String symbol, {Market market = Market.forts}) => TradingSignal(
        id: symbol,
        symbol: symbol,
        name: symbol,
        market: market,
        direction: Direction.long,
        horizon: Horizon.swing,
        horizonLabel: '',
        score: 70,
        entry: 100,
        stopLoss: 90,
        takeProfits: const [],
        priceDecimals: 0,
        riskReward: '2,0',
        chips: const [],
        note: '',
        factors: const [],
        events: const [],
        unitRisk: 10,
        unitRiskLabel: '',
        unitMultiplier: 1,
        unitDecimals: 0,
        unitName: 'конт.',
        lastPrice: '100',
        changeLabel: '',
        changeUp: true,
        status: SignalStatus.proposed,
      );

  PaperTrade openTrade(String symbol, {String strategyId = 'forts'}) => PaperTrade(
        id: symbol,
        symbol: symbol,
        strategyId: strategyId,
        long: true,
        entry: 100,
        stopLoss: 90,
        tpPrices: const [110],
        tpShares: const [100],
        score: 70,
        createdAt: now.subtract(const Duration(hours: 2)),
        status: PaperStatus.open,
      );

  PaperTrade closedTrade(String symbol, double resultR, DateTime at) => PaperTrade(
        id: '$symbol-${at.millisecondsSinceEpoch}',
        symbol: symbol,
        strategyId: 'forts',
        long: true,
        entry: 100,
        stopLoss: 90,
        tpPrices: const [110],
        tpShares: const [100],
        score: 70,
        createdAt: at.subtract(const Duration(hours: 5)),
        status: PaperStatus.closed,
        resultR: resultR,
        closedAt: at,
      );

  test('пустой журнал ничему не мешает', () {
    final decision = const RiskEngine().check(
      spec: specOf('SiU6'),
      signal: signalOf('SiU6'),
      ledger: SignalLedger(),
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isTrue);
  });

  test('лимит одновременных позиций', () {
    final ledger = SignalLedger(trades: [
      openTrade('SiU6'),
      openTrade('BRU6'),
      openTrade('MXU6'),
    ]);

    final decision = const RiskEngine().check(
      spec: specOf('GAZRU6'),
      signal: signalOf('GAZRU6'),
      ledger: ledger,
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isFalse);
    expect(decision.reason, contains('в работе'));
  });

  test('вторая идея в той же группе корреляции отклоняется', () {
    // SiU6 и CNYU6 — обе ставки на валюту: это одна позиция двойным размером.
    final ledger = SignalLedger(trades: [openTrade('SiU6')]);

    final decision = const RiskEngine().check(
      spec: specOf('CNYU6'),
      signal: signalOf('CNYU6'),
      ledger: ledger,
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isFalse);
    expect(decision.reason, contains('currency'));
  });

  test('другая группа проходит', () {
    final ledger = SignalLedger(trades: [openTrade('SiU6')]);

    final decision = const RiskEngine().check(
      spec: specOf('BTCUSDT', market: Market.crypto),
      signal: signalOf('BTCUSDT', market: Market.crypto),
      ledger: ledger,
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isTrue);
  });

  test('дневной лимит потерь закрывает торговлю', () {
    final ledger = SignalLedger(trades: [
      closedTrade('SiU6', -1, now.subtract(const Duration(hours: 4))),
      closedTrade('BRU6', -1.2, now.subtract(const Duration(hours: 3))),
    ]);

    final decision = const RiskEngine().check(
      spec: specOf('MXU6'),
      signal: signalOf('MXU6'),
      ledger: ledger,
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isFalse);
    expect(decision.reason, contains('лимит потерь'));
  });

  test('вчерашние потери сегодняшнюю торговлю не блокируют', () {
    final yesterday = now.subtract(const Duration(days: 1));
    final ledger = SignalLedger(trades: [
      closedTrade('SiU6', -1, yesterday),
      closedTrade('BRU6', -1.2, yesterday.add(const Duration(minutes: 30))),
    ]);

    // Пауза после серии стопов уже истекла — проверяем именно дневной лимит.
    final engine = const RiskEngine(
      limits: RiskLimits(pauseAfterStops: Duration(hours: 1)),
    );
    final decision = engine.check(
      spec: specOf('MXU6'),
      signal: signalOf('MXU6'),
      ledger: ledger,
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isTrue);
  });

  test('два стопа подряд включают паузу', () {
    final ledger = SignalLedger(trades: [
      closedTrade('SiU6', -1, now.subtract(const Duration(hours: 3))),
      closedTrade('BRU6', -1, now.subtract(const Duration(hours: 2))),
    ]);

    // Дневной лимит поднят, чтобы сработал именно гейт паузы.
    const engine = RiskEngine(limits: RiskLimits(dailyLossLimitR: 10));
    final decision = engine.check(
      spec: specOf('MXU6'),
      signal: signalOf('MXU6'),
      ledger: ledger,
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isFalse);
    expect(decision.reason, contains('пауза'));
  });

  test('стоп после прибыльной сделки паузу не включает', () {
    final ledger = SignalLedger(trades: [
      closedTrade('SiU6', 1.5, now.subtract(const Duration(hours: 3))),
      closedTrade('BRU6', -1, now.subtract(const Duration(hours: 2))),
    ]);

    const engine = RiskEngine(limits: RiskLimits(dailyLossLimitR: 10));
    expect(engine.pausedUntil(ledger, now), isNull);
  });

  test('объём выше доли дневного оборота отклоняется', () {
    // Риск 1000 ₽ при риске 10 пунктов на контракт — 100 контрактов по 100 ₽,
    // то есть 10 000 ₽ нотионала при обороте 100 000 ₽: это 10%, а можно 0,5%.
    final decision = const RiskEngine().check(
      spec: specOf('SiU6'),
      signal: signalOf('SiU6'),
      ledger: SignalLedger(),
      risk: risk,
      now: now,
      dailyTurnover: 100000,
    );
    expect(decision.allowed, isFalse);
    expect(decision.reason, contains('оборота'));
  });

  test('на ликвидном инструменте тот же объём проходит', () {
    final decision = const RiskEngine().check(
      spec: specOf('SiU6'),
      signal: signalOf('SiU6'),
      ledger: SignalLedger(),
      risk: risk,
      now: now,
      dailyTurnover: 100000000,
    );
    expect(decision.allowed, isTrue);
  });

  test('неизвестный оборот лимит ликвидности не выдумывает', () {
    final decision = const RiskEngine().check(
      spec: specOf('SiU6'),
      signal: signalOf('SiU6'),
      ledger: SignalLedger(),
      risk: risk,
      now: now,
    );
    expect(decision.allowed, isTrue);
  });

  test('состояние лимитов читается для интерфейса', () {
    final ledger = SignalLedger(trades: [
      openTrade('SiU6'),
      closedTrade('BRU6', -0.4, now.subtract(const Duration(hours: 1))),
    ]);

    final state = const RiskEngine().stateOf(ledger, now);
    expect(state.open, 1);
    expect(state.maxConcurrent, 3);
    expect(state.dayResultR, closeTo(-0.4, 1e-9));
    expect(state.pausedUntil, isNull);
  });
}
