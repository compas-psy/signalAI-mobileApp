import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/models/portfolio.dart';
import 'package:signalai/ui/screens/trades_screen.dart';

TradesSummary summary({
  List<double> equityCurve = const [1, 2, 3],
  List<RejectionRow> rejections = const [],
}) =>
    TradesSummary(
      equityTitle: 'Эквити · бумажная торговля',
      equityChange: '−1,2R',
      equityCurve: equityCurve,
      stats: const [StatTile(value: '61%', label: 'винрейт')],
      positions: const [
        ActivePosition(
          symbol: 'SRU6',
          direction: Direction.short,
          entryLabel: '27 520',
          currentLabel: '27 480',
          pnlLabel: '+40',
          pnlPositive: true,
          progressPercent: 0,
          stage: 'бумажная',
        ),
      ],
      journal: const [
        JournalEntry(
          date: '27.07',
          symbol: 'BTCUSDT',
          direction: Direction.long,
          outcome: 'SL',
          rMultiple: -1.2,
        ),
      ],
      rejections: rejections,
    );

Widget host(Widget child) => Directionality(
      textDirection: TextDirection.ltr,
      child: MediaQuery(
        data: const MediaQueryData(size: Size(412, 892)),
        child: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(width: 412, height: 892, child: child),
        ),
      ),
    );

void main() {
  testWidgets('отбраковка идёт после идущих и закрытых сделок', (tester) async {
    // Отбраковка — рассказ о том, чего мы НЕ сделали. Она стояла выше
    // «Бумажных» и «Журнала», то есть выше всего, что происходит с деньгами
    // прямо сейчас. Двадцать строк отказов первыми — это раздел, который
    // отвечает не на тот вопрос, с которым его открывают.
    await tester.pumpWidget(host(TradesScreen(
      summary: summary(rejections: const [
        RejectionRow(
          symbol: 'BRQ6',
          reason: 'score 45 ниже порога 60',
          moveLabel: '—',
          movePositive: true,
        ),
      ]),
    )));

    final list = find.byType(ListView).first;
    await tester.drag(list, const Offset(0, -800));
    await tester.pump();
    await tester.drag(list, const Offset(0, -800));
    await tester.pump();

    final journal = tester.getTopLeft(find.text('Журнал · 7 дней'.toUpperCase())).dy;
    final rejected = tester.getTopLeft(find.text('Отбраковано'.toUpperCase())).dy;
    expect(rejected, greaterThan(journal));
  });

  testWidgets('одна точка эквити объясняет себя, а не рисует пустоту',
      (tester) async {
    // Пустая коробка 64 px неотличима от поломки — владелец так её и прочитал.
    await tester.pumpWidget(host(TradesScreen(summary: summary(equityCurve: const [1]))));

    expect(find.textContaining('кривая появится со второй'), findsOneWidget);
  });

  testWidgets('совсем без закрытых сделок говорится прямо', (tester) async {
    await tester.pumpWidget(host(TradesScreen(summary: summary(equityCurve: const []))));

    expect(find.textContaining('Закрытых сделок ещё нет'), findsOneWidget);
  });
}
