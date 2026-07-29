import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/domain/idea/evidence.dart';
import 'package:signalai/domain/idea/idea_mapper.dart';
import 'package:signalai/domain/idea/trade_plan.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/ui/widgets/chart_layers.dart';
import 'package:signalai/ui/widgets/trade_chart.dart';

RiskBudget budget() => const RiskBudget(
      scoreRiskPercent: 1,
      remainingDailyPercent: 3,
      remainingWeeklyPercent: 5,
      remainingMonthlyPercent: 10,
      remainingOpenRiskPercent: 2,
      remainingClusterPercent: 1.25,
    );

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('разметка не обещает слоёв, которых график не умеет рисовать', () async {
    // Чип слоя, который нечем нарисовать, нажимается впустую: график не
    // меняется, и приложение выглядит сломанным. Если детектор научится
    // новому типу разметки, этот тест упадёт раньше владельца.
    final controller = AppController(DemoRepository());
    addTearDown(controller.dispose);
    await controller.load();

    final signals = controller.digest?.signals ?? const [];
    expect(signals, isNotEmpty, reason: 'нечего проверять без сигналов');

    for (final signal in signals) {
      final idea = IdeaMapper.fromSignal(
        signal,
        budget: budget(),
        equity: 2400000,
        now: DateTime.now(),
      );
      expect(
        idea.availableLayers.difference(TradeChart.renderableLayers),
        isEmpty,
        reason: '${signal.symbol}: разметка требует слоёв, которых нет у графика',
      );
    }
  });

  testWidgets('переключатель прячет метки выключенного слоя', (tester) async {
    var hidden = <ChartLayer>{};
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: StatefulBuilder(
          builder: (context, setState) => ChartLayerBar(
            available: const {
              ChartLayer.candles,
              ChartLayer.levels,
              ChartLayer.smc,
            },
            visible: {
              ChartLayer.candles,
              ChartLayer.levels,
              ChartLayer.smc,
            }.difference(hidden),
            onToggle: (layer) => setState(() {
              hidden = hidden.contains(layer)
                  ? (hidden.toSet()..remove(layer))
                  : (hidden.toSet()..add(layer));
            }),
          ),
        ),
      ),
    );

    expect(find.text('SMC'), findsOneWidget);
    await tester.tap(find.text('SMC'));
    await tester.pump();
    expect(hidden, {ChartLayer.smc});

    await tester.tap(find.text('SMC'));
    await tester.pump();
    expect(hidden, isEmpty);
  });

  testWidgets('свечи выключить нельзя', (tester) async {
    // Без свечей разметка висит в пустоте: слой помечен alwaysOn, и чип по
    // нему не должен ничего переключать.
    final toggled = <ChartLayer>[];
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: ChartLayerBar(
          available: const {ChartLayer.candles, ChartLayer.levels},
          visible: const {ChartLayer.candles, ChartLayer.levels},
          onToggle: toggled.add,
        ),
      ),
    );

    await tester.tap(find.text('Candles'));
    await tester.pump();
    expect(toggled, isEmpty);

    await tester.tap(find.text('Levels'));
    await tester.pump();
    expect(toggled, [ChartLayer.levels]);
  });

  testWidgets('один слой не показывает панель вовсе', (tester) async {
    // Переключать нечего — панель из одного неотключаемого чипа была бы
    // украшением, а не управлением.
    await tester.pumpWidget(
      Directionality(
        textDirection: TextDirection.ltr,
        child: ChartLayerBar(
          available: const {ChartLayer.candles},
          visible: const {ChartLayer.candles},
          onToggle: (_) {},
        ),
      ),
    );
    expect(find.text('Candles'), findsNothing);
  });
}
