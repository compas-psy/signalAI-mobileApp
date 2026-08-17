import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';
import 'package:signalai/state/navigation.dart';

void main() {
  Future<void> pumpApp(WidgetTester tester) async {
    tester.view.physicalSize = const Size(412, 892);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));
  }

  test('идеи открываются единой воронкой с понятными состояниями', () {
    expect(
      AppSection.ideas.pills,
      const [
        'Все',
        'Нужно решить',
        'Формируются',
        'Ждут входа',
        'Позиции открыты',
      ],
    );
  });

  testWidgets('Сегодня — cockpit, а не набор пустых KPI', (tester) async {
    await pumpApp(tester);

    expect(find.text('Капитал'.toUpperCase()), findsWidgets);
    expect(find.textContaining('НУЖНО РЕШИТЬ'), findsOneWidget);
    expect(find.textContaining('ФОРМИРУЮТСЯ'), findsOneWidget);

    // Старый нулевой KPI не должен занимать большую карточку.
    expect(find.text('Нужны решения'.toUpperCase()), findsNothing);
    // В демо нет paper-сделок: пустые секции также не занимают экран.
    expect(find.textContaining('ЖДУТ ВХОДА'), findsNothing);
    expect(find.textContaining('ПОЗИЦИИ ОТКРЫТЫ'), findsNothing);
  });
}
