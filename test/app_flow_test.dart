import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';
import 'package:signalai/data/api/engine_client.dart';
import 'package:signalai/state/app_scope.dart';
import 'package:signalai/ui/app_shell.dart';

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
      // Пилюль в разделе может быть больше, чем помещается в ширину телефона:
      // строка прокручивается, и до дальних приходится доезжать.
      final target = find.text(pill);
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

  test('без адреса движка выдача объясняет молчание, а не пустоту', () async {
    // Идеи считает сервер (§18). В сборке без его адреса показывать нечего —
    // и это обязано читаться как «движок молчит», а не как «сетапов нет»
    // (§24). Разница в действиях: чинить связь или ждать рынок.
    //
    // Проверяется на клиенте движка, а не через приложение: демо-сборка
    // намеренно подставляет идеи макета, и через неё этот случай больше не
    // воспроизвести.
    final ideas = await EngineClient().today();

    expect(ideas.isAvailable, isFalse);
    expect(ideas.ideas, isEmpty);
    expect(ideas.unavailableReason, contains('Адрес движка не задан'));
    // Ключевое: причина «нет сетапов» пуста. Заполни её здесь — и обрыв
    // связи стал бы на экране спокойным днём.
    expect(ideas.noSetupsReason, isNull);
  });

  testWidgets('демо-сборка называет себя и показывает идеи макета',
      (tester) async {
    // Демо-режим существует, чтобы смотреть интерфейс без движка. Но
    // приложение отправляет заявки, и выдуманный уровень входа выглядит как
    // настоящий — поэтому сборка обязана сказать о себе на экране.
    await pumpApp(tester);
    await goTo(tester, 'Идеи', 'Все');

    expect(find.byType(DemoBanner), findsOneWidget);
    expect(find.textContaining('данные макета'), findsOneWidget);
    // И ради чего всё: лента не пуста, разбор есть что открыть.
    expect(find.text('SiU6'), findsWidgets);
  });

  testWidgets('«Сегодня» — cockpit денег и торгового lifecycle', (tester) async {
    await pumpApp(tester);

    expect(find.text('Капитал'.toUpperCase()), findsOneWidget);
    expect(find.textContaining('НУЖНО РЕШИТЬ'), findsOneWidget);
    expect(find.textContaining('ФОРМИРУЮТСЯ'), findsOneWidget);

    // Старые большие KPI и абстрактный общий список больше не занимают экран.
    expect(find.text('Нужны решения'.toUpperCase()), findsNothing);
    expect(find.text('Идеи на контроле'.toUpperCase()), findsNothing);
    expect(find.text('Все идеи →'), findsNothing);
  });

  testWidgets('разделы и подразделы переключаются', (tester) async {
    await pumpApp(tester);

    await goTo(tester, 'Журнал');
    expect(find.text('Эквити · 30 дней'.toUpperCase()), findsOneWidget);
    expect(find.text('+8,4%'), findsOneWidget);

    await goTo(tester, 'Настройки', 'Стратегии');
    await scrollTo(tester, find.text('Запустить бэктест'));
    expect(find.text('Запустить бэктест'), findsOneWidget);

    await goTo(tester, 'Настройки', 'Подключения');
    expect(find.text('Биржи · API'.toUpperCase()), findsOneWidget);
    expect(find.text('Т-Инвестиции API'), findsOneWidget);
  });

  testWidgets('«Контроль» открывает отдельный owner control plane', (tester) async {
    await pumpApp(tester);

    await goTo(tester, 'Настройки', 'Контроль');

    expect(find.text('Контроль стратегий'), findsOneWidget);
    expect(find.text('FORTS'), findsOneWidget);
    expect(find.text('BYBIT'), findsOneWidget);
    expect(find.textContaining('read-only'), findsOneWidget);
  });

  testWidgets('бэктест обновляет статистику', (tester) async {
    await pumpApp(tester);

    await goTo(tester, 'Настройки', 'Стратегии');

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

    await goTo(tester, 'Настройки', 'Подключения');

    expect(find.text('Не подключено'), findsOneWidget);
    await tester.tap(find.text('Подключить'));
    await tester.pump(const Duration(milliseconds: 400));

    expect(find.text('Binance подключена по API-ключу'), findsOneWidget);
    expect(find.text('Подключено'), findsNWidgets(3));
  });

  testWidgets('на планшете лента и разбор стоят рядом', (tester) async {
    // Двухколоночный путь отдельный от телефонного: сломать его правкой
    // навигации легко, а заметить — только на планшете.
    tester.view.physicalSize = const Size(1100, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await tester.pumpWidget(SignalAiApp(repository: DemoRepository()));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.pump(const Duration(milliseconds: 400));

    await tester.tap(find.text('Идеи').last);
    await tester.pump(const Duration(milliseconds: 300));

    // Слева карточка идеи, справа её разбор — без единого перехода.
    expect(find.text('SiU6'), findsWidgets);
    // Имя инструмента стоит и в карточке ленты, и в шапке разбора: «SiU6»
    // знает не всякий, кто помнит, что торгует доллар к рублю.
    expect(find.text('Доллар/Рубль · сент 2026'), findsWidgets);
  });
}
