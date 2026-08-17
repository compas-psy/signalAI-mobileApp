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

    // Всё, что нужно для решения, лежит на одной ленте: до дальних блоков
    // доезжают прокруткой, а не переключателем вкладок.
    for (final label in const [
      'ПОЧЕМУ ИДЕЯ ПОЯВИЛАСЬ',
      'ПАРАМЕТРЫ СДЕЛКИ',
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
    // Оценка идеи, а не сигнала: у сигнала свой балл старого скринера, и два
    // разных числа под одним именем — расхождение показаний.
    expect(find.text('84/100'), findsOneWidget);
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
    expect(find.text('Пропустить'), findsOneWidget);
    // Подпись главной кнопки зависит от финальной проверки §11.1: пройдена —
    // явное подтверждение, нет — причина отказа прямо на кнопке. Проверяется
    // положение полосы, а не исход проверки.
    final confirm = find.text('Подтвердить план').evaluate().isNotEmpty
        ? find.text('Подтвердить план')
        : find.text('Вход заблокирован проверкой');
    expect(confirm, findsOneWidget);

    final bar = tester.getRect(find.text('Пропустить'));
    expect(bar.bottom, lessThan(892));
  });

  testWidgets('ожидание названо формированием и не имеет подтверждения',
      (tester) async {
    final controller = await pumpDetail(tester);

    // Вторая демо-идея находится в Ready: серверный эквивалент —
    // wait_for_trigger. Даже при полном плане она не должна выглядеть как
    // подтверждённая сделка.
    controller.openSignal('btc');
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.text('Формируется'), findsWidgets);
    expect(find.text('Наблюдать · ждём триггер'), findsOneWidget);
    expect(find.text('Подтвердить paper-сделку'), findsNothing);
  });

  testWidgets('пилюли ленты не висят над открытым разбором', (tester) async {
    final controller = await pumpDetail(tester);

    // Пилюли воронки — разрез списка. Над карточкой они управляли бы тем,
    // чего на экране нет.
    expect(find.byType(SectionHeader), findsNothing);
    expect(find.text('Формируются'), findsNothing);

    controller.back();
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.byType(SectionHeader), findsOneWidget);
    expect(find.text('Формируются'), findsOneWidget);
  });

  testWidgets('на широком экране пара карточек встаёт в две колонки',
      (tester) async {
    await pumpDetail(tester, size: const Size(1600, 1200));

    // Тезис и параметры сделки читаются вместе: на широком экране они рядом,
    // а не друг под другом. Пара лежит под графиком — доезжаем до неё.
    // На широком экране слева стоит лента идей со своим списком — прокрутить
    // надо разбор, а не её.
    await tester.scrollUntilVisible(
      find.text('ПОЧЕМУ ИДЕЯ ПОЯВИЛАСЬ'),
      300,
      scrollable: find
          .descendant(
            of: find.byType(IdeaDetailScreen),
            matching: find.byType(Scrollable),
          )
          .first,
      duration: const Duration(milliseconds: 60),
    );
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
      await tester.pump();

      // TileGrid строит внутреннюю сетку строками. Число уникальных x даёт
      // реальное число колонок, а не расчёт, скопированный из реализации.
      final xs = <double>{};
      for (var i = 0; i < 6; i++) {
        xs.add(tester.getTopLeft(find.byKey(ValueKey(i))).dx.roundToDouble());
      }
      return xs.length;
    }

    expect(await columnsAt(250), 2);
    expect(await columnsAt(330), 3);
  });
}
