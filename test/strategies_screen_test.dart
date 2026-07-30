import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';

/// Экран «Стратегии» после переноса на методику движка.
///
/// Владелец открыл его и увидел три пакета старого скринера с порогом
/// SignalScore 60 — то есть список стратегий, которые давно не считают идеи.
/// Экран обязан называть действующую методику и не выдавать за неё
/// исследовательский инструмент.
void main() {
  Future<void> openStrategies(WidgetTester tester) async {
    tester.view.physicalSize = const Size(412, 892);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));

    await tester.tap(find.text('Настройки').last);
    await tester.pump(const Duration(milliseconds: 300));
    final pill = find.text('Стратегии');
    final row = find.byType(SingleChildScrollView).first;
    for (var i = 0; i < 8 && pill.evaluate().isEmpty; i++) {
      await tester.drag(row, const Offset(-160, 0));
      await tester.pump(const Duration(milliseconds: 120));
    }
    await tester.ensureVisible(pill);
    await tester.pump(const Duration(milliseconds: 120));
    await tester.tap(pill);
    await tester.pump(const Duration(milliseconds: 300));
  }

  testWidgets('первой стоит методика движка, а не пакеты скринера',
      (tester) async {
    await openStrategies(tester);

    expect(find.text('МЕТОДИКА ДВИЖКА'), findsOneWidget);
    // Три стратегии §14 — те, по которым сервер ищет сетапы.
    expect(find.text('Trend Pullback Continuation'), findsOneWidget);
    expect(find.text('Breakout & Retest'), findsOneWidget);
    expect(find.text('Wyckoff Reversal'), findsOneWidget);
  });

  testWidgets('пороги и лимиты — те, по которым гаснет подтверждение',
      (tester) async {
    await openStrategies(tester);

    await tester.scrollUntilVisible(
      find.text('ПОРОГИ И РИСК ДВИЖКА'),
      240,
      scrollable: find.byType(Scrollable).first,
      duration: const Duration(milliseconds: 60),
    );

    // Числа не декоративные: по ним считается объём и гаснет кнопка.
    expect(find.text('65'), findsWidgets);
    expect(find.text('75'), findsWidgets);
    expect(find.text('1,5%'), findsWidgets);
  });

  testWidgets('скринер на устройстве честно помечен', (tester) async {
    await openStrategies(tester);

    await tester.scrollUntilVisible(
      find.text('СКРИНЕР НА УСТРОЙСТВЕ'),
      240,
      scrollable: find.byType(Scrollable).first,
      duration: const Duration(milliseconds: 60),
    );

    // Главное слово: его тумблеры на ленту идей не влияют.
    expect(find.text('не наполняет ленту'), findsOneWidget);
  });
}
