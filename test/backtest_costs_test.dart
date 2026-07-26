import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/backtester.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/trade_simulator.dart';

import 'fixtures.dart';

/// Цена издержек и строгость исполнения — то, из-за чего «прибыльная»
/// стратегия на бумаге оказывается убыточной в реальности.
void main() {
  final fireAt = fixtureStart.add(const Duration(hours: 120));

  Future<BacktestSummary> run({
    required TradingCosts Function(InstrumentSpec spec) costs,
    required double step,
    int tail = 60,
    double wick = 0.05,
  }) =>
      Backtester(screener: FakeScreener(fireAt: fireAt), costs: costs).run([
        InstrumentHistory(
          spec: cryptoSpec,
          hourly: path(step: step, tail: tail, wick: wick),
          daily: dailyWarmup(),
        ),
      ]);

  test('издержки уменьшают результат ровно на свою величину', () async {
    final clean = await run(costs: (s) => TradingCosts.none, step: 0.5);
    final real = await run(costs: (s) => defaultCostsFor(s), step: 0.5);

    expect(clean.count, 1);
    expect(real.count, 1);

    final gross = clean.trades.single.resultR;
    final net = real.trades.single.resultR;
    final cost = real.trades.single.costR;

    expect(net, lessThan(gross), reason: 'комиссия и фандинг не могут быть в плюс');
    // Главный инвариант: costR объясняет разницу целиком, ничего не теряется
    // и не приписывается дважды.
    expect(cost, closeTo(gross - net, 1e-9));
    expect(cost, greaterThan(0));
    expect(real.averageCostR, closeTo(cost, 1e-9));
  });

  test('чистый прогон без издержек ничего не списывает', () async {
    final clean = await run(costs: (s) => TradingCosts.none, step: 0.5);
    expect(clean.trades.single.costR, 0);
    expect(clean.averageCostR, 0);
  });

  test('стоп с проскальзыванием хуже, чем ровно −1R', () async {
    final real = await run(costs: (s) => defaultCostsFor(s), step: -0.5);

    expect(real.count, 1);
    expect(real.trades.single.resultR, lessThan(-1));
  });

  test('касание уровня в тик лимитку не исполняет', () async {
    // Часовик, где после сигнала цена ровно касается входа и разворачивается:
    // low каждого бара равен цене входа, за уровень она не уходит.
    final hourly = <Candle>[
      for (var i = 0; i < 121; i++) bar(i, 100, 100, wick: 0),
      // Цена стоит ровно на входе: low == entry, ни одного шага за уровень.
      for (var i = 121; i < 200; i++) bar(i, 100, 100.5, wick: 0),
    ];

    final summary = await Backtester(screener: FakeScreener(fireAt: fireAt)).run([
      InstrumentHistory(spec: cryptoSpec, hourly: hourly, daily: dailyWarmup()),
    ]);

    expect(summary.count, 0, reason: 'касание в тик очередь не проливает');
  });

  test('шаг за уровень лимитку исполняет', () async {
    // Тот же сюжет, но цена уходит на шаг ниже входа — заявка исполнена.
    final hourly = <Candle>[
      for (var i = 0; i < 121; i++) bar(i, 100, 100, wick: 0),
      for (var i = 121; i < 200; i++) bar(i, 100 - cryptoSpec.tick, 100.5, wick: 0),
    ];

    // Горизонт короткий: цель теста — факт исполнения, а не путь позиции.
    final summary = await Backtester(
      screener: FakeScreener(fireAt: fireAt),
      maxHoldBars: 20,
    ).run([
      InstrumentHistory(spec: cryptoSpec, hourly: hourly, daily: dailyWarmup()),
    ]);

    expect(summary.count, 1);
  });

  group('Безубыток после TP1', () {
    PendingOrder order({required bool breakeven}) => PendingOrder(
          long: true,
          entry: 100,
          stopLoss: 98,
          takeProfits: const [
            (price: 102.8, share: 0.5),
            (price: 104.4, share: 0.3),
            (price: 107.0, share: 0.2),
          ],
          expiresAt: 10,
          breakevenAfterTp1: breakeven,
        );

    test('после TP1 возврат к входу закрывает остаток в ноль, а не в стоп', () {
      double runTo(bool breakeven) {
        final position = OpenPosition(order: order(breakeven: breakeven), openedAt: 0, maxHoldBars: 120);
        // Бар 0: вход. Бар 1: TP1. Бар 2: откат к входу, но не до стопа.
        // Бар 3: добой до исходного стопа.
        expect(position.onBar(_bar(0, high: 100.5, low: 99.5), 0), isNull);
        expect(position.onBar(_bar(1, high: 103.0, low: 100.2), 1), isNull);
        final atEntry = position.onBar(_bar(2, high: 101.0, low: 99.9), 2);
        if (atEntry != null) return atEntry;
        return position.onBar(_bar(3, high: 99.0, low: 97.9), 3)!;
      }

      // Без правила: +0,7R за TP1 и −0,5R остатком на стопе = +0,2R.
      expect(runTo(false), closeTo(0.7 - 0.5, 1e-9));
      // С правилом: остаток вышел по входу — TP1 остался заработанным.
      expect(runTo(true), closeTo(0.7, 1e-9));
    });

    test('безубыток не действует тем же баром, где взят тейк', () {
      final position = OpenPosition(order: order(breakeven: true), openedAt: 0, maxHoldBars: 120);
      expect(position.onBar(_bar(0, high: 100.5, low: 99.5), 0), isNull);
      // Один бар задел и TP1, и вход: порядок внутри бара неизвестен, и
      // закрывать остаток «по входу» этим же баром движок не имеет права.
      final result = position.onBar(_bar(1, high: 103.0, low: 99.8), 1);
      expect(result, isNull, reason: 'позиция ещё жива');
      expect(position.nextTp, 1);
      // А со следующего бара защита уже в безубытке.
      final closed = position.onBar(_bar(2, high: 101.0, low: 99.9), 2);
      expect(closed, closeTo(0.7, 1e-9));
      expect(position.stoppedAtBreakeven, isTrue);
    });

    test('до TP1 стоп стоит на исходном месте', () {
      final position = OpenPosition(order: order(breakeven: true), openedAt: 0, maxHoldBars: 120);
      expect(position.onBar(_bar(0, high: 100.5, low: 99.5), 0), isNull);
      // Возврат к входу без взятого тейка позицию не трогает.
      expect(position.onBar(_bar(1, high: 100.4, low: 99.9), 1), isNull);
      final stopped = position.onBar(_bar(2, high: 99.5, low: 97.9), 2);
      expect(stopped, closeTo(-1, 1e-9));
      expect(position.stoppedAtBreakeven, isFalse);
    });
  });

  group('Стоп-вход по пробою', () {
    PendingOrder order({required bool stopEntry}) => PendingOrder(
          long: true,
          entry: 101,
          stopLoss: 99,
          takeProfits: const [(price: 103.8, share: 1.0)],
          expiresAt: 10,
          stopEntry: stopEntry,
        );

    test('лонг-лимитка и лонг-стоп исполняются с разных сторон уровня', () {
      // Бар целиком НИЖЕ уровня: лимитка на откате наполнилась бы, стоп-вход
      // не должен активироваться — цена не показала силу.
      final below = _bar(0, high: 100.8, low: 100.2);
      expect(order(stopEntry: false).crossedBy(below), isTrue);
      expect(order(stopEntry: true).crossedBy(below), isFalse);

      // Бар прошёл ВЫШЕ уровня: сила подтверждена — стоп-вход активен,
      // лимитка на откате осталась бы неисполненной.
      final above = _bar(1, high: 101.5, low: 101.1);
      expect(order(stopEntry: true).crossedBy(above), isTrue);
      expect(order(stopEntry: false).crossedBy(above), isFalse);
    });

    test('стоп-вход платит проскальзывание на входе, лимитка — нет', () {
      final costs = TradingCosts(slippage: 0.2);
      double entryCost(bool stopEntry) => OpenPosition(
            order: PendingOrder(
              long: true,
              entry: 101,
              stopLoss: 99,
              takeProfits: const [(price: 103.8, share: 1.0)],
              expiresAt: 10,
              costs: costs,
              stopEntry: stopEntry,
            ),
            openedAt: 0,
            maxHoldBars: 120,
          ).costR;

      expect(entryCost(true), closeTo(0.1, 1e-9), reason: '0,2 пункта на риск 2');
      expect(entryCost(false), 0);
    });
  });

  test('профиль издержек различается по рынку', () {
    final crypto = defaultCostsFor(cryptoSpec);
    final forts = defaultCostsFor(fortsSpec);

    expect(crypto.feeRate, greaterThan(0), reason: 'Bybit берёт процент от оборота');
    expect(crypto.fundingPerBar, greaterThan(0));
    expect(forts.feeRate, 0, reason: 'на срочном рынке сбор фиксированный');
    expect(forts.feePoints, greaterThan(0));
    expect(forts.fundingPerBar, 0, reason: 'фандинга на FORTS нет');
  });
}

/// Свеча с заданными краями — движок безразличен к источнику баров.
Candle _bar(int hour, {required double high, required double low, double? close}) => Candle(
      time: DateTime.utc(2026, 7, 1).add(Duration(hours: hour)),
      open: (high + low) / 2,
      high: high,
      low: low,
      close: close ?? (high + low) / 2,
      volume: 100,
    );
