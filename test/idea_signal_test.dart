import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';
import 'package:signalai/domain/idea/trade_plan.dart';

Idea _idea({TradePlan? plan, String id = 'CRYPTO:PERP:BTCUSDT'}) => Idea(
      id: 'idea-1',
      instrumentId: id,
      instrumentName: 'Биткоин к доллару · бессрочный',
      market: Market.crypto,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'v1',
      state: IdeaState.triggered,
      score: const QualityScore(contributions: [], total: 84),
      createdAt: DateTime(2026, 7, 29),
      validUntil: DateTime(2026, 7, 30),
      thesis: 'тезис',
      plan: plan,
      timeframes: const ['1d', '4h', '1h'],
    );

TradePlan _plan() => TradePlan(
      instrumentId: 'CRYPTO:PERP:BTCUSDT',
      direction: Direction.long,
      orderType: PlanOrderType.stopLimit,
      entryLow: 118100.5,
      entryHigh: 118400.5,
      maxSlippagePercent: 0.1,
      stop: 115800.5,
      targets: const [
        PlanTarget(name: 'TP1', price: 121400.5, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP2', price: 124800.5, fraction: 0.4, afterFill: ''),
        PlanTarget(name: 'TP3', price: 129000.5, fraction: 0.2, afterFill: ''),
      ],
      quantity: 9,
      lotSize: 1,
      valuePerPoint: 1,
      riskRubles: 17200,
      riskPercent: 0.72,
      marginEstimate: 96000,
      expiry: DateTime(2026, 7, 30),
      strategyVersion: 'v1',
    );

/// Перевод идеи движка в сигнал для экранов.
///
/// Разбор идеи устроен вокруг `TradingSignal`, а идеи приходят с движка:
/// общих идентификаторов с дайджестом у них нет. Без перевода нажатие по
/// карточке не открывало ничего либо открывало **чужой** инструмент.
void main() {
  test('тикер вынимается из канонического идентификатора', () {
    // «CRYPTO:PERP:BTCUSDT» в заголовке обрезается на «CRYPTO:PER…» — по
    // такой подписи нельзя понять, о каком активе идея.
    expect(EngineContract.symbolOf('CRYPTO:PERP:BTCUSDT'), 'BTCUSDT');
    expect(EngineContract.symbolOf('MOEX:FUT:SIU6'), 'SIU6');
    expect(EngineContract.symbolOf('SBER'), 'SBER');
  });

  test('идея переносится в сигнал вместе с уровнями плана', () {
    final signal = EngineContract.signalFrom(_idea(plan: _plan()));

    expect(signal.id, 'idea-1');
    expect(signal.symbol, 'BTCUSDT');
    expect(signal.name, 'Биткоин к доллару · бессрочный');
    expect(signal.direction, Direction.long);
    expect(signal.score, 84);
    // Опорная цена входа — середина зоны.
    expect(signal.entry, 118250.5);
    expect(signal.stopLoss, 115800.5);
    expect(signal.takeProfits, hasLength(3));
    expect(signal.takeProfits.first.sharePercent, 40);
    // Вход по пробою — это стоп-заявка, и сторона исполнения от этого зависит.
    expect(signal.entryIsStop, isTrue);
  });

  test('знаки после запятой считаются по самим ценам', () {
    // Округлить чужую цену до целых значит показать не ту цену.
    expect(EngineContract.signalFrom(_idea(plan: _plan())).priceDecimals, 1);
  });

  test('идея без плана не притворяется сделкой', () {
    final signal = EngineContract.signalFrom(_idea());

    // Уровней нет — и выдумывать их нечем. Ноль здесь честнее подстановки.
    expect(signal.entry, 0);
    expect(signal.stopLoss, 0);
    expect(signal.takeProfits, isEmpty);
    expect(signal.riskReward, isEmpty);
  });

  test('котировки не подставляются нулём', () {
    final signal = EngineContract.signalFrom(_idea(plan: _plan()));

    // Ноль на месте цены читается как обвал. Пусто — значит пусто.
    expect(signal.lastPrice, isEmpty);
    expect(signal.changeLabel, isEmpty);
  });
}
