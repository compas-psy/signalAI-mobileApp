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
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/idea/paper_position.dart';
import 'package:signalai/ui/screens/ideas_screen.dart';
import 'package:signalai/ui/widgets/common.dart';

void main() {
  PaperPosition trade({
    String signalId = '',
    bool pending = false,
    double? unrealizedR = 0.13,
    double? currentStop,
    DateTime? breakevenAt,
    int? staleHours,
    bool fromServer = false,
    bool resultRealized = false,
  }) =>
      PaperPosition(
        id: 'p1',
        ideaId: signalId,
        symbol: 'BTCUSDT',
        long: false,
        pending: pending,
        entry: 64038.30,
        initialStop: 65208.20,
        currentStop: currentStop ?? 65208.20,
        tpPrices: const [64469.4, 64199.3, 63929.1],
        tpsTaken: 0,
        breakevenAt: breakevenAt,
        resultR: unrealizedR,
        staleHours: staleHours,
        fromServer: fromServer,
        resultRealized: resultRealized,
      );

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
      PaperPositionCard(trade: trade(pending: true)),
    );
    expect(find.textContaining('цена до неё не дошла'), findsOneWidget);
    expect(find.textContaining('в позиции с'), findsNothing);
  });

  testWidgets('сделка без записанной идеи говорит об этом прямо',
      (tester) async {
    // Журнал ведёт и скринер на устройстве: позиция по тому же тикеру, что и
    // идея движка, — это другая сделка с другим входом, и путать их нельзя.
    await pump(tester, PaperPositionCard(trade: trade()));
    expect(find.textContaining('идеи за ней не записано'), findsOneWidget);
  });

  testWidgets('ссылка на идею есть, а идеи в выдаче нет — это другое',
      (tester) async {
    // Две разные причины «открывать нечего»: у сделки нет ссылки вовсе —
    // свойство сделки; ссылка есть, а идеи в ленте нет — свойство ленты.
    await pump(tester, PaperPositionCard(trade: trade(signalId: 'i1')));
    expect(find.textContaining('нет в текущей выдаче'), findsOneWidget);
  });

  testWidgets('ссылка без идеи не делает карточку нажимаемой', (tester) async {
    // Та самая недоделка: надпись смотрела на выдачу, а нажатие — на то,
    // записан ли идентификатор. Сделка со ссылкой, но без идеи открывала
    // пустой разбор под подписью «идеи за ней нет».
    await pump(tester, PaperPositionCard(trade: trade(signalId: 'i1')));
    expect(tester.widget<Pressable>(find.byType(Pressable)).onTap, isNull);
  });

  testWidgets('сделка скринера с открываемым разбором не врёт «идей нет»',
      (tester) async {
    // Идеи движка и сигналы дайджеста — два разных множества, а карточка
    // смотрела только в первое. Сделка скринера устройства несла
    // идентификатор своего сигнала, но подписывалась «идей по ней нет» и не
    // нажималась — при том, что разбор существовал.
    await pump(
      tester,
      PaperPositionCard(trade: trade(signalId: 's1'), onOpenIdea: () {}),
    );

    expect(find.textContaining('нет в текущей выдаче'), findsNothing);
    expect(find.textContaining('разбор открыт'), findsOneWidget);
    expect(tester.widget<Pressable>(find.byType(Pressable)).onTap, isNotNull);
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
    final source = EngineContract.idea({
      'id': 'i1',
      'instrument_id': 'CRYPTO:PERP:BTCUSDT',
      'symbol': 'BTCUSDT',
      'strategy': 'TREND_PULLBACK',
      'direction': 'SHORT',
      'status': 'WATCH',
      'quality_status': 'OK',
      'horizon_days': 5,
      'score': '80',
      'signal_time': '2026-07-31T09:00:00Z',
      'expires_at': '2026-08-05T09:00:00Z',
    });
    await pump(
      tester,
      PaperPositionCard(
        trade: trade(signalId: 'i1'),
        idea: source,
        onOpenIdea: () => opened++,
      ),
    );
    // Происхождение названо стратегией идеи, а не «идеи нет».
    expect(find.textContaining('из идеи'), findsOneWidget);
    await tester.tap(find.byType(PaperPositionCard));
    await tester.pump();
    expect(opened, 1);
  });

  // ── Чем защищена позиция и жива ли сверка ────────────────────────────────

  testWidgets('текущий стоп виден, и безубыток назван безубытком',
      (tester) async {
    // Стоп на устройстве был одним неизменяемым полем, а перенос в безубыток
    // жил во временной переменной внутри пересчёта — то есть не существовал
    // ни в базе, ни на экране. Владелец видел исходный стоп даже тогда,
    // когда позиция фактически была защищена.
    await pump(
      tester,
      PaperPositionCard(
        trade: trade(
          currentStop: 64038.30,
          breakevenAt: DateTime.utc(2026, 7, 31, 14),
          fromServer: true,
        ),
      ),
    );

    expect(find.textContaining('стоп в безубытке'), findsOneWidget);
    expect(find.textContaining('65 208'), findsNothing);
  });

  testWidgets('без переноса стоп называется просто стопом', (tester) async {
    await pump(tester, PaperPositionCard(trade: trade()));

    expect(find.textContaining('стоп в безубытке'), findsNothing);
    expect(find.textContaining('стоп '), findsOneWidget);
  });

  testWidgets('замершая сверка названа вслух', (tester) async {
    // Корень зависшей BTCUSDT: Bybit ответил телефону 403, свечей нет,
    // сверка не вызывалась ни разу — и позиция несколько дней выглядела
    // живой, показывая «взято тейков: 2 из 3».
    await pump(
      tester,
      PaperPositionCard(trade: trade(staleHours: 52, fromServer: true)),
    );

    expect(find.textContaining('сверка не идёт 2 дн'), findsOneWidget);
  });

  testWidgets('свежая сверка экран не засоряет', (tester) async {
    await pump(tester, PaperPositionCard(trade: trade(staleHours: 1)));

    expect(find.textContaining('сверка не идёт'), findsNothing);
  });

  testWidgets('зафиксированное не выдаётся за плавающее', (tester) async {
    // Сервер считает сумму по уже взятым целям без открытого остатка,
    // устройство — сколько сделка стоит целиком, если закрыть сейчас. Числа
    // разные, подпись «R» была одна.
    await pump(
      tester,
      PaperPositionCard(trade: trade(resultRealized: true, fromServer: true)),
    );
    expect(find.text('зафикс.'), findsOneWidget);

    await pump(tester, PaperPositionCard(trade: trade()));
    expect(find.text('зафикс.'), findsNothing);
  });

  testWidgets('видно, кто ведёт сделку', (tester) async {
    // У серверной сопровождение идёт круглосуточно, у местной — только пока
    // открыто приложение и биржа отвечает телефону. Это разная надёжность.
    await pump(tester, PaperPositionCard(trade: trade(fromServer: true)));
    expect(find.text('ведёт сервер'), findsOneWidget);

    await pump(tester, PaperPositionCard(trade: trade()));
    expect(find.text('бумажная'), findsOneWidget);
  });
}
