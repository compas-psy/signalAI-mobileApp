import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/idea/evidence.dart';
import 'package:signalai/ui/widgets/chart_layers.dart';
import 'package:signalai/ui/widgets/trade_chart.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('график умеет рисовать каждый слой из словаря разметки §10.6', () {
    // Чип слоя, который нечем нарисовать, нажимается впустую: график не
    // меняется, и приложение выглядит сломанным. Проверка идёт по всему
    // словарю типов, а не по выдаче конкретного дня: детектор, научившийся
    // новому типу, уронит этот тест раньше, чем владелец увидит пустой чип.
    final needed = {
      for (final type in AnnotationType.values) type.layer,
    };
    expect(
      needed.difference(TradeChart.renderableLayers),
      isEmpty,
      reason: 'словарь §10.6 требует слоёв, которых график не рисует',
    );
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
