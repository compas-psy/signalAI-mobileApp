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
