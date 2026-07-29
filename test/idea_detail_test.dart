import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/app_scope.dart';
import 'package:signalai/ui/screens/idea_detail_screen.dart';
import 'package:signalai/ui/screens/ideas_screen.dart';
import 'package:signalai/ui/widgets/segmented.dart';
import 'package:signalai/ui/widgets/section_header.dart';

/// Разбор идеи по прототипу `design/prototype_v21.html`.
///
/// Проверяется не оформление, а устройство экрана: что решение принимается на
/// одной ленте, что подтверждение всегда под рукой и что фильтры ленты не
/// висят над открытой карточкой.
void main() {
  /// Экран телефона из макета — 412×892.
  Future<AppController> pumpDetail(WidgetTester tester,
      {Size size = const Size(412, 892)}) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    // Демо-репозиторий отвечает с задержкой; график анимируется бесконечно,
    // поэтому ждём фиксированными кадрами, а не pumpAndSettle.
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));

    await tester.tap(find.text('Идеи').last);
    await tester.pump(const Duration(milliseconds: 300));

    // Лента идей приходит от движка, а в демо-сборке его нет. Идею открываем
    // тем же вызовом, что и её карточка: проверяем разбор, а не список.
    final controller = AppScope.read(tester.element(find.byType(IdeasScreen)));
    controller.openSignal(controller.currentSignal!.id);
    await tester.pump(const Duration(milliseconds: 400));
    return controller;
  }

  testWidgets('разбор — одна лента, а не переключатель вкладок', (tester) async {
    await pumpDetail(tester);

    expect(find.byType(IdeaDetailScreen), findsOneWidget);
    // Сегменты «План · Оценка · События» убраны: решение об отправке ордера
    // принимается по всем трём сразу, а вкладка прятала две трети ответа.
    expect(find.byType(SegmentedControl), findsNothing);

    // Всё, что нужно для решения, лежит на одной ленте: до разбора оценки и
    // событий доезжают прокруткой, а не переключателем.
    expect(find.text('ПОЧЕМУ ИДЕЯ ПОЯВИЛАСЬ'), findsOneWidget);
    expect(find.text('ПАРАМЕТРЫ СДЕЛКИ'), findsOneWidget);
    for (final label in const [
      'ИЗ ЧЕГО СОБРАНА ОЦЕНКА',
      'ВНЕШНИЕ СОБЫТИЯ',
      'КОГДА ИДЕЯ ОТМЕНЯЕТСЯ',
    ]) {
      await tester.scrollUntilVisible(
        find.text(label),
        260,
        scrollable: find.byType(Scrollable).first,
        duration: const Duration(milliseconds: 60),
      );
      expect(find.text(label), findsOneWidget);
    }
  });

  testWidgets('шапка называет состояние, срок и рынок', (tester) async {
    await pumpDetail(tester);

    expect(find.text('SiU6'), findsOneWidget);
    expect(find.text('87/100'), findsOneWidget);
    expect(find.text('оценка качества'), findsOneWidget);
    // Рынок — чипом контекста, как в прототипе.
    expect(find.text('FORTS'), findsOneWidget);
    // Имя инструмента не теряется: «SiU6» знает не всякий.
    expect(find.text('Доллар/Рубль · сент 2026'), findsOneWidget);
  });

  testWidgets('полоса подтверждения видна без прокрутки', (tester) async {
    await pumpDetail(tester);

    // Кнопка стоит поверх ленты, а не последней строкой: до неё не нужно
    // долистывать разбор.
    expect(find.text('Подтвердить план'), findsOneWidget);
    expect(find.text('Пропустить'), findsOneWidget);

    final bar = tester.getRect(find.text('Подтвердить план'));
    expect(bar.bottom, lessThan(892));
  });

  testWidgets('пилюли ленты не висят над открытым разбором', (tester) async {
    final controller = await pumpDetail(tester);

    // «Решения · Наблюдение · В работе» — разрез списка. Над карточкой они
    // управляли бы тем, чего на экране нет.
    expect(find.byType(SectionHeader), findsNothing);
    expect(find.text('Наблюдение'), findsNothing);

    controller.back();
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.byType(SectionHeader), findsOneWidget);
    expect(find.text('Наблюдение'), findsOneWidget);
  });

  testWidgets('на широком экране пара карточек встаёт в две колонки',
      (tester) async {
    await pumpDetail(tester, size: const Size(1600, 1200));

    // Тезис и параметры сделки читаются вместе: на широком экране они рядом,
    // а не друг под другом.
    final thesis = tester.getRect(find.text('ПОЧЕМУ ИДЕЯ ПОЯВИЛАСЬ'));
    final plan = tester.getRect(find.text('ПАРАМЕТРЫ СДЕЛКИ'));
    expect(plan.left, greaterThan(thesis.left));
    expect((plan.top - thesis.top).abs(), lessThan(4));
  });

  testWidgets('сетка плиток считает колонки по ширине блока', (tester) async {
    Future<int> columnsAt(double width) async {
      await tester.pumpWidget(Directionality(
        textDirection: TextDirection.ltr,
        child: Center(
          child: SizedBox(
            width: width,
            child: TileGrid(
              minTileWidth: 100,
              tiles: [
                for (var i = 0; i < 6; i++)
                  SizedBox(key: ValueKey(i), height: 20),
              ],
            ),
          ),
        ),
      ));
      final tops = {
        for (var i = 0; i < 6; i++)
          tester.getTopLeft(find.byKey(ValueKey(i))).dy,
      };
      return 6 ~/ tops.length;
    }

    expect(await columnsAt(340), 3);
    expect(await columnsAt(220), 2);
    expect(await columnsAt(660), 6);
  });
}
