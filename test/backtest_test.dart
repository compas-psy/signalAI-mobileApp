import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/backtester.dart';
import 'package:signalai/domain/analysis/trade_simulator.dart';

import 'fixtures.dart';

/// Движок исполнения тестируется отдельно от поиска сетапов: скринер покрыт
/// `screener_test.dart`, а здесь подставной скринер стреляет в заданный бар —
/// и проверяется механика лимитки, стопа, частичных тейков и агрегатов.
///
/// Издержки здесь выключены намеренно: их цена меряется в
/// `backtest_costs_test.dart`, и смешивать два предмета в одном тесте значит
/// не проверить толком ни один.
void main() {
  final fireAt = fixtureStart.add(const Duration(hours: 120));

  test('лимитка исполняется, тейки собираются по долям объёма', () async {
    final summary =
        await Backtester(screener: FakeScreener(fireAt: fireAt), costs: noCosts).run([
      InstrumentHistory(spec: fortsSpec, hourly: path(step: 0.5), daily: dailyWarmup()),
    ]);

    expect(summary.count, 1);
    // 50% на 1,4R + 30% на 2,2R + 20% на 3,5R = 2,06R.
    expect(summary.trades.single.resultR, closeTo(2.06, 1e-9));
    expect(summary.winRate, 100);
    expect(summary.profitFactor, isNull, reason: 'убытков не было — PF не определён');
    expect(summary.equityCurve.single, closeTo(2.06, 1e-9));
  });

  test('стоп срабатывает по худшему случаю и даёт −1R', () async {
    final summary =
        await Backtester(screener: FakeScreener(fireAt: fireAt), costs: noCosts).run([
      InstrumentHistory(spec: fortsSpec, hourly: path(step: -0.5), daily: dailyWarmup()),
    ]);

    expect(summary.count, 1);
    expect(summary.trades.single.resultR, closeTo(-1, 1e-9));
    expect(summary.winRate, 0);
  });

  test('боковик после сигнала закрывается по горизонту около нуля', () async {
    final summary = await Backtester(
      screener: FakeScreener(fireAt: fireAt),
      maxHoldBars: 24,
      costs: noCosts,
    ).run([
      InstrumentHistory(
        spec: fortsSpec,
        hourly: path(step: 0, tail: 80),
        daily: dailyWarmup(),
      ),
    ]);

    expect(summary.count, 1);
    expect(summary.trades.single.resultR.abs(), lessThan(0.1));
  });

  test('пустой список инструментов не падает', () async {
    final summary = await const Backtester().run(const []);
    expect(summary.count, 0);
    expect(summary.profitFactor, isNull);
  });
}
