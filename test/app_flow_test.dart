import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';
import 'package:signalai/state/app_scope.dart';

void main() {
  /// Экран телефона из макета — 412×892.
  Future<void> pumpApp(WidgetTester tester) async {
    tester.view.physicalSize = const Size(412, 892);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    // Демо-репозиторий отвечает с задержкой; график анимируется бесконечно,
    // поэтому ждём фиксированными кадрами, а не pumpAndSettle. После дайджеста
    // контроллер перечитывает «Сделки» — ждём и этот хвост, иначе его таймер
    // переживает тест.
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));
  }

  /// Переходит в раздел нижней навигации, затем в его пилюлю.
  ///
  /// Навигация версии 3: пять разделов внизу, подразделы — пилюли в шапке.
  Future<void> goTo(WidgetTester tester, String section, [String? pill]) async {
    await tester.tap(find.text(section).last);
    await tester.pump(const Duration(milliseconds: 300));
    if (pill != null) {
      await tester.tap(find.text(pill));
      await tester.pump(const Duration(milliseconds: 300));
    }
  }

  /// Прокручивает текущий список до виджета: сначала пока он не будет
  /// построен, затем доводит его в видимую область — список строит немного
  /// больше, чем показывает, и тап по такому виджету не попадает.
  Future<void> scrollTo(WidgetTester tester, Finder target) async {
    for (var i = 0; i < 25 && target.evaluate().isEmpty; i++) {
      await tester.drag(find.byType(ListView).first, const Offset(0, -260));
      await tester.pump(const Duration(milliseconds: 120));
    }
    await tester.ensureVisible(target);
    await tester.pump(const Duration(milliseconds: 120));
  }

  group('Пушнутый маршрут', () {
    /// Открывает новый маршрут поверх приложения и отдаёт его контекст.
    ///
    /// Ровно тот путь, которым открываются диагностика, сырой ответ биржи и
    /// разбор пакета: `Navigator.push` от корневого навигатора.
    Future<BuildContext> pushRoute(WidgetTester tester) async {
      late BuildContext pushed;
      final navigator = tester.state<NavigatorState>(find.byType(Navigator).first);
      unawaited(navigator.push(PageRouteBuilder<void>(
        pageBuilder: (context, _, _) {
          pushed = context;
          return const SizedBox.shrink();
        },
      )));
      await tester.pump(const Duration(milliseconds: 400));
      return pushed;
    }

    testWidgets('видит AppScope, а не серый прямоугольник', (tester) async {
      // AppScope жил внутри `home:`, то есть под корневым Navigator: маршрут
      // вставал рядом с ним, контроллер оттуда не доставался. В релизе assert
      // вырезан, срабатывал null-check — и Flutter рисовал ErrorWidget, то
      // есть пустое серое окно. Так «пропал» разбор пакета.
      await pumpApp(tester);
      final context = await pushRoute(tester);

      expect(AppScope.read(context), isNotNull);
    });

    testWidgets('текст без жёлтого подчёркивания', (tester) async {
      // MaterialApp подставляет тексту вне Material свой «ошибочный» стиль.
      // Наши стили задают цвет и размер, но не decoration — поэтому от него
      // наследовалось ровно жёлтое двойное подчёркивание на всём экране.
      await pumpApp(tester);
      final context = await pushRoute(tester);

      expect(
        DefaultTextStyle.of(context).style.decoration,
        isNot(TextDecoration.underline),
      );
    });
  });

  testWidgets('AppScope без предка объясняет себя, а не падает null-check',
      (tester) async {
    await tester.pumpWidget(Builder(
      builder: (context) {
        expect(
          () => AppScope.read(context),
          throwsA(isA<FlutterError>().having(
            (e) => e.message,
            'message',
            contains('AppScope не найден'),
          )),
        );
        return const SizedBox.shrink();
      },
    ));
  });

  testWidgets('дайджест показывает режим рынка и пять идей', (tester) async {
    await pumpApp(tester);
    await goTo(tester, 'Торговля');

    expect(find.text('Утренний дайджест'), findsOneWidget);
    expect(find.text('Сб, 25 июля · 10:10 МСК'), findsOneWidget);
    expect(find.text('5 из 5'), findsOneWidget);
    expect(find.text('SiU6'), findsOneWidget);
    expect(find.text('IMOEX'), findsOneWidget);
  });

  testWidgets('карточка идеи открывается по тапу', (tester) async {
    await pumpApp(tester);
    await goTo(tester, 'Торговля');

    await tester.tap(find.text('SiU6'));
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.text('Доллар/Рубль · сент 2026'), findsOneWidget);
    expect(find.text('Тейк-профиты'.toUpperCase()), findsOneWidget);

    // Объём считается от депозита 2 400 000 ₽ и риска 0,75%.
    await scrollTo(tester, find.text('32 конт.'));
    expect(find.text('32 конт.'), findsOneWidget);

    await scrollTo(tester, find.text('Отправить на биржу'));
    expect(find.text('Отправить на биржу'), findsOneWidget);
  });

  testWidgets('подтверждение выставляет ордер и показывает тост', (tester) async {
    await pumpApp(tester);
    await goTo(tester, 'Торговля');

    await tester.tap(find.text('SiU6'));
    await tester.pump(const Duration(milliseconds: 300));

    await scrollTo(tester, find.text('Отправить на биржу'));
    await tester.tap(find.text('Отправить на биржу'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Исполнить на бирже'), findsOneWidget);
    expect(find.text('Риск, если SL'), findsOneWidget);

    await tester.tap(find.text('Исполнить на бирже'));
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.text('Ордер отправлен · OCO SL + TP выставлены'), findsOneWidget);
    expect(
      find.text('В работе · лимитный ордер и OCO (SL + 3 TP) выставлены'),
      findsOneWidget,
    );
  });

  testWidgets('сверка с площадками доступна и без книги', (tester) async {
    // Замкнутый круг, из-за которого на устройстве нечем было начать: блок
    // здоровья данных с единственной кнопкой сверки рисовался только при
    // непустой книге, а наполнить книгу можно было только этой кнопкой.
    await pumpApp(tester);

    await scrollTo(tester, find.text('Сверить с площадками'));
    expect(find.text('Здоровье данных'.toUpperCase()), findsOneWidget);
    expect(find.text('Сверить с площадками'), findsWidgets);
  });

  testWidgets('разделы и подразделы переключаются', (tester) async {
    await pumpApp(tester);

    // Приложение открывается на «Сегодня»: состояние капитала, а не лента.
    expect(find.text('Очередь решений'.toUpperCase()), findsOneWidget);

    await goTo(tester, 'Торговля', 'Позиции');
    expect(find.text('Эквити · 30 дней'.toUpperCase()), findsOneWidget);
    expect(find.text('+8,4%'), findsOneWidget);

    await goTo(tester, 'Лаборатория');
    await scrollTo(tester, find.text('Запустить бэктест'));
    expect(find.text('Бэктест'.toUpperCase()), findsOneWidget);
    expect(find.text('Запустить бэктест'), findsOneWidget);

    await goTo(tester, 'Контроль', 'Интеграции');
    expect(find.text('Биржи · API'.toUpperCase()), findsOneWidget);
    expect(find.text('Т-Инвестиции API'), findsOneWidget);
  });

  testWidgets('бэктест обновляет статистику', (tester) async {
    await pumpApp(tester);

    await goTo(tester, 'Лаборатория');

    await scrollTo(tester, find.text('Запустить бэктест'));
    await tester.tap(find.text('Запустить бэктест'));
    await tester.pump(const Duration(milliseconds: 100));
    expect(find.text('Считаем…'), findsOneWidget);

    await tester.pump(const Duration(milliseconds: 1800));
    expect(find.text('Бэктест завершён · PF 2,0'), findsOneWidget);
    expect(find.text('+47%'), findsOneWidget);
  });

  testWidgets('Binance подключается из настроек', (tester) async {
    await pumpApp(tester);

    await goTo(tester, 'Контроль', 'Интеграции');

    expect(find.text('Не подключено'), findsOneWidget);
    await tester.tap(find.text('Подключить'));
    await tester.pump(const Duration(milliseconds: 400));

    expect(find.text('Binance подключена по API-ключу'), findsOneWidget);
    expect(find.text('Подключено'), findsNWidgets(3));
  });
}
