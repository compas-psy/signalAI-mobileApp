import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/state/navigation.dart';

void main() {
  group('Разделы версии 3', () {
    test('у «Сегодня» подразделов нет — это один ответ, а не меню', () {
      expect(AppSection.today.pills, isEmpty);
    });

    test('глубина не превышает двух уровней', () {
      // Раздел → пилюля → разбор объекта. Третьего уровня нет ни у кого:
      // пять разделов по пять экранов — двадцать пять адресов на одного
      // пользователя, и утро уходит на поиск.
      for (final section in AppSection.values) {
        expect(section.pills.length, lessThanOrEqualTo(5));
      }
    });

    test('состав подразделов совпадает с ТЗ', () {
      expect(AppSection.capital.pills,
          ['Обзор', 'Счета', 'Пакеты', 'Книга', 'Аналитика']);
      expect(AppSection.trading.pills, ['Идеи', 'Позиции', 'Опционы', 'Журнал']);
      expect(AppSection.lab.pills, ['Стратегии', 'Скринер РФ']);
      expect(AppSection.control.pills,
          ['Риск и лимиты', 'Интеграции', 'Уведомления', 'Безопасность']);
    });

    test('индексы пилюль совпадают с перечислениями', () {
      expect(AppSection.capital.pills.length, CapitalPill.values.length);
      expect(AppSection.trading.pills.length, TradingPill.values.length);
      expect(AppSection.lab.pills.length, LabPill.values.length);
      expect(AppSection.control.pills.length, ControlPill.values.length);
    });
  });

  group('Миграция маршрутов версии 2', () {
    test('старые вкладки ведут в свои новые места', () {
      expect(AppRoute.fromLegacy('ideas'),
          const AppRoute(AppSection.trading, 0));
      expect(AppRoute.fromLegacy('trades'),
          AppRoute(AppSection.trading, TradingPill.positions.index));
      expect(AppRoute.fromLegacy('invest'),
          AppRoute(AppSection.lab, LabPill.screener.index));
      expect(AppRoute.fromLegacy('strategies'),
          AppRoute(AppSection.lab, LabPill.strategies.index));
      expect(AppRoute.fromLegacy('settings'), const AppRoute(AppSection.control, 0));
    });

    test('неизвестный маршрут ведёт на «Сегодня», а не падает', () {
      expect(AppRoute.fromLegacy('что-то своё'), const AppRoute(AppSection.today));
    });
  });

  group('Режим риск-движка', () {
    test('открывать новое можно только в NORMAL и CAUTION', () {
      expect(RiskMode.normal.allowsOpening, isTrue);
      expect(RiskMode.caution.allowsOpening, isTrue);
      expect(RiskMode.reduceOnly.allowsOpening, isFalse);
      expect(RiskMode.killSwitch.allowsOpening, isFalse);
    });

    test('у каждого режима есть человеческое объяснение', () {
      for (final mode in RiskMode.values) {
        expect(mode.hint, isNotEmpty, reason: 'режим без причины бесполезен');
      }
    });
  });
}
