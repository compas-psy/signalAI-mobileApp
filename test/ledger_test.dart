import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';
import 'package:signalai/domain/models/signal.dart';

final start = DateTime.utc(2026, 3, 2, 10);

TradingSignal signal({double entry = 100, double stop = 98}) => TradingSignal(
      id: 'tst',
      symbol: 'TSTU6',
      name: 'Тест',
      market: Market.forts,
      direction: Direction.long,
      horizon: Horizon.swing,
      horizonLabel: '',
      score: 80,
      entry: entry,
      stopLoss: stop,
      takeProfits: [
        TakeProfit(index: 1, price: entry + 2.8, sharePercent: 50),
        TakeProfit(index: 2, price: entry + 4.4, sharePercent: 30),
        TakeProfit(index: 3, price: entry + 7.0, sharePercent: 20),
      ],
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
      status: SignalStatus.pushed,
    );

List<Candle> bars(List<double> closes, {DateTime? from}) {
  final t0 = from ?? start.add(const Duration(hours: 1));
  return [
    for (var i = 0; i < closes.length; i++)
      Candle(
        time: t0.add(Duration(hours: i)),
        open: i == 0 ? closes[i] : closes[i - 1],
        high: (i == 0 ? closes[i] : (closes[i] > closes[i - 1] ? closes[i] : closes[i - 1])) + 0.05,
        low: (i == 0 ? closes[i] : (closes[i] < closes[i - 1] ? closes[i] : closes[i - 1])) - 0.05,
        close: closes[i],
        volume: 100,
      ),
  ];
}

void main() {
  test('сигнал записывается один раз, пока запись жива', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);
    ledger.record(signal(), start.add(const Duration(hours: 1)));
    expect(ledger.trades, hasLength(1));
  });

  test('рост до всех тейков закрывает бумажную сделку в плюс', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);

    // Цена приходит к лимитке (100), потом растёт до TP3 (107).
    final closes = [100.0, 99.9, 101.0, 103.0, 105.0, 107.5, 108.0];
    ledger.reconcile({'TSTU6': bars(closes)});

    final trade = ledger.trades.single;
    expect(trade.status, PaperStatus.closed);
    expect(trade.outcome, 'TP3');
    // 0.5·1.4 + 0.3·2.2 + 0.2·3.5 = 2.06
    expect(trade.resultR, closeTo(2.06, 1e-9));
    expect(ledger.winRate, 100);
    expect(ledger.totalR, closeTo(2.06, 1e-9));
  });

  test('падение до стопа даёт −1R и попадает в статистику', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);

    final closes = [100.0, 99.0, 98.5, 97.5];
    ledger.reconcile({'TSTU6': bars(closes)});

    final trade = ledger.trades.single;
    expect(trade.status, PaperStatus.closed);
    expect(trade.outcome, 'SL');
    expect(trade.resultR, closeTo(-1, 1e-9));
  });

  test('цена не дошла до лимитки за сутки — идея отменена', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);

    final closes = List<double>.filled(30, 103.0);
    ledger.reconcile({'TSTU6': bars(closes)});

    expect(ledger.trades.single.status, PaperStatus.cancelled);
    expect(ledger.closed, isEmpty);
  });

  test('открытая позиция показывает плавающий результат', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);

    final closes = [100.0, 99.9, 101.0, 102.0];
    ledger.reconcile({'TSTU6': bars(closes)});

    final trade = ledger.trades.single;
    expect(trade.status, PaperStatus.open);
    expect(trade.unrealizedR, closeTo(1.0, 0.1));
  });

  test('сверка детерминирована: повторный прогон не меняет итог', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);
    final candles = bars([100.0, 99.0, 98.5, 97.5]);
    ledger.reconcile({'TSTU6': candles});
    final first = ledger.trades.single.resultR;
    ledger.reconcile({'TSTU6': candles});
    expect(ledger.trades.single.resultR, first);
    expect(ledger.trades, hasLength(1));
  });

  test('журнал переживает сериализацию', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start);
    ledger.recordRejection('BRQ6', 'мало истории', 70.5, start);
    ledger.reconcile({'TSTU6': bars([100.0, 99.0, 98.5, 97.5])});

    final restored = SignalLedger.fromJson(ledger.toJson());
    expect(restored.trades.single.resultR, closeTo(-1, 1e-9));
    expect(restored.rejected.single.symbol, 'BRQ6');
  });

  test('отбраковка получает форвард-ход за 24 часа', () {
    final ledger = SignalLedger();
    ledger.recordRejection('TSTU6', 'score ниже порога', 100, start);

    // 30 часов свечей: через 24 часа цена 110 → ход +10%.
    final closes = [for (var i = 0; i < 30; i++) 100.0 + i * 0.5];
    ledger.reconcile({'TSTU6': bars(closes)});

    expect(ledger.rejected.single.movePercent24h, isNotNull);
    expect(ledger.rejected.single.movePercent24h!, greaterThan(5));
  });
}
