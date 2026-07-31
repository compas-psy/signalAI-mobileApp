/// «В работе» и журнал говорили разное об одном инструменте.
///
/// Журнал показывал открытую бумажную позицию по BTCUSDT, лента идей держала
/// BTCUSDT в «Наблюдении», а вкладка «В работе» писала «открытых позиций
/// нет». Три раздела приложения, три ответа на один вопрос — и ни одного
/// способа понять, какой верный.
///
/// Причина в том, что вкладка отвечала не на тот вопрос: она показывала идеи
/// движка в состоянии Active, а «что у меня открыто» живёт в журнале сделок.
library;

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';
import 'package:signalai/ui/screens/ideas_screen.dart';
import 'package:signalai/ui/widgets/common.dart';

void main() {
  PaperTrade trade({
    String signalId = '',
    PaperStatus status = PaperStatus.open,
    double? unrealizedR = 0.13,
  }) {
    final t = PaperTrade(
      id: 'p1',
      signalId: signalId,
      symbol: 'BTCUSDT',
      strategyId: 'trend',
      long: false,
      entry: 64038.30,
      stopLoss: 65208.20,
      tpPrices: const [64469.4, 64199.3, 63929.1],
      tpShares: const [40, 40, 20],
      score: 80,
      createdAt: DateTime.utc(2026, 7, 31, 9),
    );
    t.status = status;
    t.unrealizedR = unrealizedR;
    return t;
  }

  Future<void> pump(WidgetTester tester, Widget child) => tester.pumpWidget(
        Directionality(
          textDirection: TextDirection.ltr,
          child: MediaQuery(
            data: const MediaQueryData(size: Size(400, 900)),
            child: Align(
              alignment: Alignment.topCenter,
              child: SizedBox(width: 380, child: child),
            ),
          ),
        ),
      );

  testWidgets('открытая позиция названа позицией, а не идеей', (tester) async {
    await pump(tester, PaperPositionCard(trade: trade()));
    expect(find.text('BTCUSDT'), findsOneWidget);
    expect(find.textContaining('в позиции с'), findsOneWidget);
    // Плавающий результат — то же число, что в журнале.
    expect(find.text('+0,13R'), findsOneWidget);
  });

  testWidgets('выставленная заявка отличается от исполненной', (tester) async {
    await pump(
      tester,
      PaperPositionCard(trade: trade(status: PaperStatus.pending)),
    );
    expect(find.textContaining('цена до неё не дошла'), findsOneWidget);
    expect(find.textContaining('в позиции с'), findsNothing);
  });

  testWidgets('сделка без идеи говорит об этом прямо', (tester) async {
    // Журнал ведёт и скринер на устройстве: позиция по тому же тикеру, что и
    // идея движка, — это другая сделка с другим входом, и путать их нельзя.
    await pump(tester, PaperPositionCard(trade: trade()));
    expect(
      find.textContaining('идеи движка за ней нет'),
      findsOneWidget,
    );
  });

  testWidgets('карточка без идеи не притворяется нажимаемой', (tester) async {
    await pump(tester, PaperPositionCard(trade: trade()));
    final pressable = tester.widget<Pressable>(find.byType(Pressable));
    // Не «нажимается и ничего не делает», а не нажимается вовсе: кнопка без
    // последствия читается как сломанная.
    expect(pressable.onTap, isNull);
  });

  testWidgets('нажатие по сделке с идеей открывает разбор', (tester) async {
    var opened = 0;
    await pump(
      tester,
      PaperPositionCard(
        trade: trade(signalId: 'i1'),
        onOpenIdea: () => opened++,
      ),
    );
    await tester.tap(find.byType(PaperPositionCard));
    await tester.pump();
    expect(opened, 1);
  });
}
