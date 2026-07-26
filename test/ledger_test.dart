import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/analysis/trade_simulator.dart';
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

  test('после TP1 возврат к входу закрывается в безубыток с честной подписью', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start, breakevenAfterTp1: true);
    // Вход на 100, TP1 на 102,8 — затем откат к входу, но не до стопа 98.
    ledger.reconcile({
      'TSTU6': bars([100.0, 101.5, 103.0, 101.0, 99.8, 99.9]),
    });

    final trade = ledger.trades.single;
    expect(trade.status, PaperStatus.closed);
    expect(trade.outcome, 'TP1·БУ');
    // Заработанное на TP1 осталось: 50% на 1,4R.
    expect(trade.resultR, closeTo(0.7, 1e-9));
  });

  test('старые записи живут без правила безубытка', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start); // как записывала прошлая версия
    final restored = SignalLedger.fromJson(ledger.toJson());
    ledger.reconcile({'TSTU6': bars([100.0, 101.5, 103.0, 101.0, 99.8, 97.5])});
    restored.reconcile({'TSTU6': bars([100.0, 101.5, 103.0, 101.0, 99.8, 97.5])});

    // Обе доехали до исходного стопа: +0,7R за TP1 и −0,5R остатком.
    expect(ledger.trades.single.resultR, closeTo(0.2, 1e-9));
    expect(restored.trades.single.resultR, closeTo(0.2, 1e-9));
  });

  test('правило безубытка переживает сериализацию', () {
    final ledger = SignalLedger();
    ledger.record(signal(), start, breakevenAfterTp1: true);
    final restored = SignalLedger.fromJson(ledger.toJson());
    restored.reconcile({'TSTU6': bars([100.0, 101.5, 103.0, 101.0, 99.8, 99.9])});

    expect(restored.trades.single.outcome, 'TP1·БУ');
    expect(restored.trades.single.resultR, closeTo(0.7, 1e-9));
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

  test('журнал считает те же издержки, что и бэктест', () {
    // Инвариант всего анализа: бумажная статистика и прогон меряют одну
    // стратегию. Если бы журнал торговал без комиссий, сравнивать его с
    // бэктестом было бы бессмысленно.
    const costs = TradingCosts(feeRate: 0.00055, slippage: 0.01);

    final clean = SignalLedger()..record(signal(), start);
    final real = SignalLedger()
      ..record(signal(), start, costs: costs, fillMargin: 0.01);

    final closes = [100.0, 99.9, 101.0, 103.0, 105.0, 107.5, 108.0];
    clean.reconcile({'TSTU6': bars(closes)});
    real.reconcile({'TSTU6': bars(closes)});

    expect(clean.trades.single.status, PaperStatus.closed);
    expect(real.trades.single.status, PaperStatus.closed);
    expect(real.totalR, lessThan(clean.totalR));
  });

  test('издержки переживают сериализацию журнала', () {
    const costs = TradingCosts(feeRate: 0.00055, feePoints: 0.5, slippage: 0.01);
    final ledger = SignalLedger()
      ..record(signal(), start, costs: costs, fillMargin: 0.01);

    final restored = SignalLedger.fromJson(ledger.toJson());
    final trade = restored.trades.single;
    expect(trade.costs.feeRate, closeTo(0.00055, 1e-12));
    expect(trade.costs.feePoints, closeTo(0.5, 1e-12));
    expect(trade.fillMargin, closeTo(0.01, 1e-12));
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
