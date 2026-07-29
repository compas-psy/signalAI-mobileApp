import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';
import 'package:signalai/state/navigation.dart';

void main() {
  /// Экран телефона из макета.
  Future<void> pumpApp(WidgetTester tester) async {
    tester.view.physicalSize = const Size(412, 892);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));
  }

  /// Доезжает до пилюли в горизонтальной строке и нажимает её.
  Future<void> tapPill(WidgetTester tester, String label) async {
    final target = find.text(label);
    final row = find.byType(SingleChildScrollView).first;
    for (var i = 0; i < 8 && target.evaluate().isEmpty; i++) {
      await tester.drag(row, const Offset(-160, 0));
      await tester.pump(const Duration(milliseconds: 120));
    }
    await tester.ensureVisible(target);
    await tester.pump(const Duration(milliseconds: 120));
    await tester.tap(target);
    await tester.pump(const Duration(milliseconds: 300));
  }

  testWidgets('ни один подраздел настроек не открывается пустым',
      (tester) async {
    // Пустой экран без единого слова читается как поломка: владелец не может
    // отличить «здесь пока нечего показывать» от «приложение сломалось».
    // Так и вышло с «Режимом»: в режиме без торгового контура подраздел
    // не рисовал ничего вообще.
    await pumpApp(tester);
    await tester.tap(find.text('Настройки').last);
    await tester.pump(const Duration(milliseconds: 300));

    for (final pill in AppSection.settings.pills) {
      await tapPill(tester, pill);

      // Заголовок раздела и подписи навигации есть всегда — считаем только
      // содержимое подраздела.
      final chrome = {
        'Настройки',
        'Сегодня',
        'Портфель',
        'Идеи',
        'Журнал',
        ...AppSection.settings.pills,
      };
      final content = find
          .byType(Text)
          .evaluate()
          .map((e) => (e.widget as Text).data)
          .whereType<String>()
          .where((t) => t.trim().isNotEmpty && !chrome.contains(t))
          .toList();

      expect(content, isNotEmpty,
          reason: 'подраздел «$pill» открылся пустым экраном');
    }

    // Демо-репозиторий отвечает с задержкой; переходы по подразделам
    // успевают запустить несколько таких ответов. Даём им завершиться и
    // только потом снимаем дерево — иначе тест падает на проверке
    // незакрытых таймеров, а не на существе.
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pump(const Duration(seconds: 10));
  });
}
