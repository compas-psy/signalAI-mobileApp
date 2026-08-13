import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/engine_contract.dart';
import 'package:signalai/data/api/live_idea_source.dart';
import 'package:signalai/data/mock/demo_ideas.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/ui/widgets/idea_chart_card.dart';
import 'package:signalai/ui/widgets/trade_chart.dart';

class _FlakyLiveSource extends LiveIdeaSource {
  static int attempts = 0;

  @override
  Future<LiveIdeaData> load(Idea idea, {required String timeframe}) async {
    attempts += 1;
    if (attempts == 1) return const LiveIdeaData();
    return LiveIdeaData(
      generatedAt: DateTime.utc(2026, 8, 13, 12),
      liveOverlay: true,
      chart: SignalChart(
        timeframeLabel: '1h',
        candles: [
          ChartCandle(100, 102, 99, 101, DateTime.utc(2026, 8, 13, 10)),
          ChartCandle(101, 103, 100, 102, DateTime.utc(2026, 8, 13, 11)),
        ],
      ),
    );
  }
}

void main() {
  testWidgets('ошибочный график можно повторно запросить из карточки',
      (tester) async {
    _FlakyLiveSource.attempts = 0;
    final idea = DemoIdeas.all(DateTime.utc(2026, 8, 13)).first;
    final signal = EngineContract.signalFrom(idea);
    final layers = idea.availableLayers;

    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: SizedBox(
          width: 420,
          child: IdeaChartCard(
            signal: signal,
            idea: idea,
            timeframe: '4h',
            failed: true,
            failureReason: 'временный сбой',
            liveSource: _FlakyLiveSource(),
            available: layers,
            visible: layers,
            highlight: const {},
            onToggle: (_) {},
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.pump();

    expect(_FlakyLiveSource.attempts, 1);
    expect(find.text('Повторить'), findsOneWidget);
    expect(find.textContaining('временный сбой'), findsOneWidget);

    await tester.tap(find.text('Повторить'));
    await tester.pump();
    await tester.pump();

    expect(_FlakyLiveSource.attempts, 2);
    expect(find.text('Повторить'), findsNothing);
    expect(find.byType(TradeChart), findsOneWidget);
  });
}
