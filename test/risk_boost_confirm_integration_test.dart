import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/settings.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/ui/widgets/confirm_sheet.dart';

void main() {
  testWidgets(
    'SAI-047 PAPER confirm exposes a separate risk action without executing the trade',
    (tester) async {
      var executeCalls = 0;
      var riskCalls = 0;

      await tester.pumpWidget(_host(
        ConfirmSheet(
          signal: _signal(),
          risk: _risk(),
          paperOnly: true,
          busy: false,
          onExecute: () => executeCalls += 1,
          onRiskBoost: () => riskCalls += 1,
          onClose: () {},
        ),
      ));

      expect(find.text('Рискнуть'), findsOneWidget);
      await tester.tap(find.text('Рискнуть'));
      await tester.pump();

      expect(riskCalls, 1);
      expect(executeCalls, 0);
      expect(find.text('Подтвердить paper-сделку'), findsOneWidget);
    },
  );

  testWidgets(
    'SAI-047 non-PAPER confirm never exposes manual risk boost',
    (tester) async {
      var riskCalls = 0;

      await tester.pumpWidget(_host(
        ConfirmSheet(
          signal: _signal(),
          risk: _risk(),
          paperOnly: false,
          busy: false,
          onExecute: () {},
          onRiskBoost: () => riskCalls += 1,
          onClose: () {},
        ),
      ));

      expect(find.text('Рискнуть'), findsNothing);
      expect(riskCalls, 0);
    },
  );
}

Widget _host(Widget child) => MaterialApp(
      home: Scaffold(
        body: SizedBox.expand(child: child),
      ),
    );

TradingSignal _signal() => const TradingSignal(
      id: 'sai-047-signal',
      symbol: 'BTCUSDT',
      name: 'Bitcoin',
      market: Market.crypto,
      direction: Direction.long,
      horizon: Horizon.swing,
      horizonLabel: '1–3 дня',
      score: 91,
      entry: 100,
      stopLoss: 95,
      takeProfits: [
        TakeProfit(index: 1, price: 105, sharePercent: 40),
        TakeProfit(index: 2, price: 110, sharePercent: 40),
        TakeProfit(index: 3, price: 115, sharePercent: 20),
      ],
      priceDecimals: 2,
      riskReward: '2,0',
      chips: [],
      note: 'server-owned test fixture',
      factors: [],
      events: [],
      unitRisk: 5,
      unitRiskLabel: '5 USDT / BTC',
      unitMultiplier: 1,
      unitDecimals: 3,
      unitName: 'BTC',
      lastPrice: '100',
      changeLabel: '+1,0%',
      changeUp: true,
      status: SignalStatus.pushed,
    );

RiskProfile _risk() => const RiskProfile(
      deposit: 100000,
      riskPercent: 0.5,
      dailyLossLimit: '−2% · автостоп',
      maxConcurrentTrades: 'до 3',
      pauseRule: 'пауза до завтра',
    );
