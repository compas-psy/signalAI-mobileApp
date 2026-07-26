import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/backtester.dart';
import 'package:signalai/domain/analysis/indicators.dart';
import 'package:signalai/domain/analysis/factor_audit.dart';
import 'package:signalai/domain/analysis/screener.dart';
import 'package:signalai/domain/analysis/trade_simulator.dart';

import 'fixtures.dart';

/// Прогон обязан видеть тот же режим рынка, что видел бы телефон в тот час.
/// Иначе бэктест меряет стратегию, которой не существует.
void main() {
  test('растущий якорь даёт растущий режим в конце истории', () {
    final timeline = RegimeTimeline.build(index: dailyTrend(step: 1));

    final late = timeline.at(fixtureStart);
    expect(late.indexTrend, StructureTrend.up);
    expect(late.cryptoTrend, StructureTrend.flat, reason: 'якоря крипты не было');
  });

  test('падающий якорь даёт падающий режим', () {
    final timeline = RegimeTimeline.build(index: dailyTrend(step: -1));
    expect(timeline.at(fixtureStart).indexTrend, StructureTrend.down);
  });

  test('до накопления истории режим нейтральный, а не угаданный', () {
    final timeline = RegimeTimeline.build(index: dailyTrend(step: 1));
    // Момент раньше самой первой дневки: знать тренд ещё неоткуда.
    final early = timeline.at(fixtureStart.subtract(const Duration(days: 365)));
    expect(early.indexTrend, StructureTrend.flat);
  });

  test('короткая история якоря шкалу не создаёт', () {
    final timeline = RegimeTimeline.build(index: dailyTrend(step: 1, count: 5));
    expect(timeline.at(fixtureStart).indexTrend, StructureTrend.flat);
  });

  test('пустая шкала — всегда нейтральный режим', () {
    expect(RegimeTimeline.empty.at(fixtureStart).indexTrend, StructureTrend.flat);
    expect(RegimeTimeline.empty.at(fixtureStart).currencyTrend, StructureTrend.flat);
  });

  test('режим доезжает до скринера в прогоне', () async {
    // Скринер-шпион запоминает, с каким режимом его позвали.
    final seen = <StructureTrend>{};
    final spy = _RegimeSpy(seen: seen);

    await Backtester(screener: spy, costs: noCosts).run(
      [
        InstrumentHistory(
          spec: fortsSpec,
          hourly: path(step: 0.5),
          daily: dailyWarmup(),
        ),
      ],
      regime: RegimeTimeline.build(index: dailyTrend(step: -1)),
    );

    expect(seen, contains(StructureTrend.down),
        reason: 'прогон обязан видеть исторический режим, а не нейтральный');
  });
}

/// Скринер, который ничего не находит, но фиксирует переданный режим.
class _RegimeSpy extends Screener {
  const _RegimeSpy({required this.seen});

  final Set<StructureTrend> seen;

  @override
  ScreenerResult? evaluate(
    InstrumentInput input,
    MarketRegime regime, {
    DateTime? now,
    List<RejectedCandidate>? rejected,
    List<FactorEdge>? factorHistory,
  }) {
    seen.add(regime.forMarket(input.spec.market));
    return null;
  }
}
