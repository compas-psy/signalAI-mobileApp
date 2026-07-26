import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/main.dart';

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

  testWidgets('дайджест показывает режим рынка и пять идей', (tester) async {
    await pumpApp(tester);

    expect(find.text('Утренний дайджест'), findsOneWidget);
    expect(find.text('Сб, 25 июля · 10:10 МСК'), findsOneWidget);
    expect(find.text('5 из 5'), findsOneWidget);
    expect(find.text('SiU6'), findsOneWidget);
    expect(find.text('IMOEX'), findsOneWidget);
  });

  testWidgets('карточка идеи открывается по тапу', (tester) async {
    await pumpApp(tester);

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

  testWidgets('вкладки переключаются', (tester) async {
    await pumpApp(tester);

    await tester.tap(find.text('Сделки'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Эквити · 30 дней'.toUpperCase()), findsOneWidget);
    expect(find.text('+8,4%'), findsOneWidget);

    await tester.tap(find.text('Стратегии'));
    await tester.pump(const Duration(milliseconds: 300));
    await scrollTo(tester, find.text('Запустить бэктест'));
    expect(find.text('Бэктест'.toUpperCase()), findsOneWidget);
    expect(find.text('Запустить бэктест'), findsOneWidget);

    await tester.tap(find.text('Настройки'));
    await tester.pump(const Duration(milliseconds: 300));
    expect(find.text('Биржи · API'.toUpperCase()), findsOneWidget);
    expect(find.text('Т-Инвестиции API'), findsOneWidget);
  });

  testWidgets('бэктест обновляет статистику', (tester) async {
    await pumpApp(tester);

    await tester.tap(find.text('Стратегии'));
    await tester.pump(const Duration(milliseconds: 300));

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

    await tester.tap(find.text('Настройки'));
    await tester.pump(const Duration(milliseconds: 300));

    expect(find.text('Не подключено'), findsOneWidget);
    await tester.tap(find.text('Подключить'));
    await tester.pump(const Duration(milliseconds: 400));

    expect(find.text('Binance подключена по API-ключу'), findsOneWidget);
    expect(find.text('Подключено'), findsNWidgets(3));
  });
}
