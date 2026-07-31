import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/domain/idea/evidence.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/ui/widgets/idea_chart_card.dart';

/// Переключатель таймфрейма на графике идеи.
///
/// Кнопки 1d / 4h / 1h стояли на экране с самого начала и не нажимались:
/// свечи приезжали одним рядом, кэш был по идее, и переключать было нечего.
/// Владелец видел три кнопки, жал по ним и не получал ничего — по такому
/// поведению справедливо решают, что приложение сломано.
void main() {
  Map<String, dynamic> detail() => {
        'id': 'i1',
        'instrument_id': 'CRYPTO:PERP:ETHUSDT',
        'symbol': 'ETHUSDT',
        'strategy': 'WYCKOFF_REVERSAL',
        'direction': 'LONG',
        'status': 'WATCH',
        'quality_status': 'OK',
        'horizon_days': 5,
        'score': '84',
        'signal_time': '2026-07-30T09:00:00Z',
        'expires_at': '2026-08-03T09:00:00Z',
        'context_timeframe': '1d',
        'setup_timeframe': '4h',
        'trigger_timeframe': '1h',
        'plan': {
          'order_intent': 'LIMIT_RETEST',
          'entry_low': '1858.12',
          'entry_high': '1874.02',
          'entry_reference': '1866.07',
          'stop': '1845.91',
          'tp1': '1896.94',
          'tp2': '1935.76',
          'tp3': '2013.40',
          'rr_tp1': '1.5',
          'rr_tp2': '3.5',
          'invalidation': 'закрытие ниже границы',
        },
      };

  SignalChart candles(String label) => SignalChart(
        timeframeLabel: label,
        candles: [
          for (var i = 0; i < 30; i++)
            ChartCandle(
              1860 + i.toDouble(),
              1870 + i.toDouble(),
              1850 + i.toDouble(),
              1865 + i.toDouble(),
              DateTime.utc(2026, 7, 30, 9).add(Duration(hours: i)),
            ),
        ],
      );

  Future<List<String>> tapPill(WidgetTester tester, String label) async {
    final idea = EngineContract.idea(detail());
    final picked = <String>[];
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: MediaQuery(
          data: const MediaQueryData(size: Size(400, 900)),
          child: Align(
            alignment: Alignment.topCenter,
            child: SizedBox(
              width: 380,
              child: IdeaChartCard(
                signal: EngineContract.signalFrom(idea),
                idea: idea,
                chart: candles('1h'),
                timeframe: '1h',
                onTimeframe: picked.add,
                available: const {ChartLayer.candles, ChartLayer.levels},
                visible: const {ChartLayer.candles, ChartLayer.levels},
                highlight: const {},
                onToggle: (_) {},
              ),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text(label));
    await tester.pump();
    return picked;
  }

  testWidgets('нажатие по 1d просит именно дневные свечи', (tester) async {
    expect(await tapPill(tester, '1d'), ['1d']);
  });

  testWidgets('нажатие по 4h просит четырёхчасовые', (tester) async {
    expect(await tapPill(tester, '4h'), ['4h']);
  });

  testWidgets('по активному таймфрейму запрос не уходит', (tester) async {
    // Повторная загрузка того же ряда — трата трафика и мигание картинки.
    expect(await tapPill(tester, '1h'), isEmpty);
  });

  testWidgets('пока свечи грузятся, панель остаётся на месте', (tester) async {
    final idea = EngineContract.idea(detail());
    final picked = <String>[];
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: MediaQuery(
          data: const MediaQueryData(size: Size(400, 900)),
          child: Align(
            alignment: Alignment.topCenter,
            child: SizedBox(
              width: 380,
              child: IdeaChartCard(
                signal: EngineContract.signalFrom(idea),
                idea: idea,
                timeframe: '1d',
                loading: true,
                onTimeframe: picked.add,
                available: const {ChartLayer.candles, ChartLayer.levels},
                visible: const {ChartLayer.candles, ChartLayer.levels},
                highlight: const {},
                onToggle: (_) {},
              ),
            ),
          ),
        ),
      ),
    );
    // Панель не исчезла вместе с графиком, и видно, чего именно ждём.
    expect(find.text('1h'), findsOneWidget);
    expect(find.text('Загружаем свечи 1d…'), findsOneWidget);

    // Второй таймфрейм в это время не запрашивается: два ответа на один
    // график приходят вразнобой, и нарисован будет последний, а не нажатый.
    await tester.tap(find.text('4h'));
    await tester.pump();
    expect(picked, isEmpty);
  });

  testWidgets('подсвечен тот таймфрейм, чьи свечи нарисованы', (tester) async {
    // Карточка открывается сводкой, у которой таймфреймов ещё нет, и первый
    // график грузится часовым. Потом приезжает полная карточка, сетапным
    // оказывается 4h — и тулбар подсвечивал «4h» поверх часовых свечей,
    // споря с тегом «1h», который график честно рисует в своём углу.
    final idea = EngineContract.idea(detail());
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: MediaQuery(
          data: const MediaQueryData(size: Size(400, 900)),
          child: Align(
            alignment: Alignment.topCenter,
            child: SizedBox(
              width: 380,
              child: IdeaChartCard(
                signal: EngineContract.signalFrom(idea),
                idea: idea,
                chart: candles('1h'),
                // Экран просит 4h, источник дал часовые.
                timeframe: '4h',
                onTimeframe: (_) {},
                available: const {ChartLayer.candles, ChartLayer.levels},
                visible: const {ChartLayer.candles, ChartLayer.levels},
                highlight: const {},
                onToggle: (_) {},
              ),
            ),
          ),
        ),
      ),
    );

    final pill = tester.widget<Text>(
      find.descendant(of: find.byType(IdeaChartCard), matching: find.text('1h')),
    );
    // Активная пилюля жирнее и светлее — это и есть «1h», а не «4h».
    expect(pill.style?.fontWeight, FontWeight.w600);
    final other = tester.widget<Text>(
      find.descendant(of: find.byType(IdeaChartCard), matching: find.text('4h')),
    );
    expect(other.style?.fontWeight, isNot(FontWeight.w600));
  });

  testWidgets('отказ источника назван, а не показан пустым местом',
      (tester) async {
    final idea = EngineContract.idea(detail());
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: MediaQuery(
          data: const MediaQueryData(size: Size(400, 900)),
          child: Align(
            alignment: Alignment.topCenter,
            child: SizedBox(
              width: 380,
              child: IdeaChartCard(
                signal: EngineContract.signalFrom(idea),
                idea: idea,
                timeframe: '1d',
                failed: true,
                onTimeframe: (_) {},
                available: const {ChartLayer.candles, ChartLayer.levels},
                visible: const {ChartLayer.candles, ChartLayer.levels},
                highlight: const {},
                onToggle: (_) {},
              ),
            ),
          ),
        ),
      ),
    );
    expect(
      find.textContaining('Свечей 1d источник не дал'),
      findsOneWidget,
    );
  });
}
