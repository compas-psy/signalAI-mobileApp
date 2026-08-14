import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/market/idea_chart_source.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/domain/enums.dart';
import 'package:signalai/domain/idea/idea.dart';
import 'package:signalai/domain/idea/idea_state.dart';
import 'package:signalai/domain/idea/quality_score.dart';
import 'package:signalai/domain/models/signal.dart';
import 'package:signalai/state/app_controller.dart';

class _FlakyChartSource extends IdeaChartSource {
  int attempts = 0;

  @override
  Future<SignalChart?> load(
    Idea idea, {
    String timeframe = '',
    void Function(String reason)? onFailure,
  }) async {
    attempts += 1;
    if (attempts == 1) {
      onFailure?.call('временный сетевой сбой');
      return null;
    }
    return SignalChart(
      timeframeLabel: timeframe,
      candles: [
        ChartCandle(100, 102, 99, 101, DateTime.utc(2026, 8, 13, 10)),
        ChartCandle(101, 103, 100, 102, DateTime.utc(2026, 8, 13, 14)),
      ],
    );
  }
}

Idea _idea() => Idea(
      id: 'chart-retry',
      instrumentId: 'MOEX:FUT:TEST',
      instrumentName: 'Test',
      market: Market.forts,
      direction: Direction.long,
      strategy: SetupStrategy.trendPullback,
      strategyVersion: 'test',
      state: IdeaState.watch,
      score: const QualityScore.empty(),
      createdAt: DateTime.utc(2026, 8, 13, 9),
      validUntil: DateTime.utc(2026, 8, 14, 9),
      thesis: '',
      plan: null,
      timeframes: const ['1d', '4h', '1h'],
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('повтор после отказа не уведомляет синхронно и снова спрашивает источник',
      () async {
    final source = _FlakyChartSource();
    final controller = AppController(
      DemoRepository(),
      chartSource: source,
    );
    addTearDown(controller.dispose);
    final idea = _idea();

    await controller.loadIdeaChart(idea);
    expect(source.attempts, 1);
    expect(controller.ideaChartFailed(idea), isTrue);

    var notifications = 0;
    controller.addListener(() => notifications += 1);
    final retry = controller.loadIdeaChart(idea);

    // loadIdeaChart вызывается из build карточки. До первого асинхронного
    // yield он не должен уведомлять дерево, иначе Flutter получает
    // «setState/markNeedsBuild during build».
    expect(notifications, 0);
    await retry;

    expect(source.attempts, 2);
    expect(controller.ideaChartFailed(idea), isFalse);
    expect(controller.ideaChart(idea.id, timeframe: '4h'), isNotNull);
    expect(notifications, greaterThan(0));
  });
}
