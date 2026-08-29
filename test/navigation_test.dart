import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/state/app_controller.dart';
import 'package:signalai/state/navigation.dart';

import 'support/offline_http.dart';

void main() {
  // Книга спрашивает у платформы каталог для файла — без инициализации
  // биндинга канал недоступен и чтение падает ещё до диска.
  TestWidgetsFlutterBinding.ensureInitialized();

  group('Разделы по ТЗ v2', () {
    test('у «Сегодня» подразделов нет — это один ответ, а не меню', () {
      expect(AppSection.today.pills, isEmpty);
    });

    test('разделов ровно пять и в порядке ТЗ', () {
      expect(AppSection.values.map((s) => s.name).toList(),
          ['today', 'portfolio', 'ideas', 'journal', 'settings']);
    });

    test('глубина не превышает двух уровней', () {
      // Раздел → пилюля → разбор объекта. Третьего уровня нет ни у кого:
      // иначе утро уходит на поиск нужного экрана, а не на решение.
      for (final section in AppSection.values) {
        expect(section.pills.length, lessThanOrEqualTo(7), reason: section.title);
      }
    });

    test('индексы пилюль совпадают с видимыми перечислениями', () {
      // SettingsPill.values содержит mode/security только для legacy-миграции.
      // IdeasPill — тоже legacy-фильтр локального анализатора. Видимый thin UX
      // использует отдельную пятиступенчатую IdeaFunnelPill.
      expect(AppSection.portfolio.pills.length, PortfolioPill.values.length);
      expect(AppSection.ideas.pills.length, IdeaFunnelPill.values.length);
      expect(AppSection.journal.pills.length, JournalPill.values.length);
      expect(AppSection.settings.pills.length, SettingsPill.thinValues.length);
      expect(AppSection.ideas.pills,
          ['Все', 'Нужно решить', 'Формируются', 'Ждут входа', 'Позиции открыты']);
      expect(AppSection.settings.pills, [
        'Риск',
        'Контроль',
        'Подключения',
        'Стратегии',
        'Уведомления',
        'Данные',
      ]);
      expect(SettingsPill.thinValues, [
        SettingsPill.risk,
        SettingsPill.control,
        SettingsPill.connections,
        SettingsPill.strategies,
        SettingsPill.notifications,
        SettingsPill.data,
      ]);
    });
  });

  group('Миграция сохранённых маршрутов', () {
    test('прежние вкладки ведут в свои новые места', () {
      expect(AppRoute.fromLegacy('ideas'), const AppRoute(AppSection.ideas));
      expect(AppRoute.fromLegacy('trades'), const AppRoute(AppSection.journal));
      expect(AppRoute.fromLegacy('invest'), const AppRoute(AppSection.portfolio));
      expect(AppRoute.fromLegacy('capital'), const AppRoute(AppSection.portfolio));
      // После добавления owner Control «Стратегии» остаются отдельной пилюлей.
      expect(AppRoute.fromLegacy('strategies'),
          const AppRoute(AppSection.settings, 3));
      expect(AppRoute.fromLegacy('lab'), const AppRoute(AppSection.settings, 1));
      expect(AppRoute.fromLegacy('control'), const AppRoute(AppSection.settings, 1));
      expect(AppRoute.fromLegacy('settings'), const AppRoute(AppSection.settings));
    });

    test('неизвестный маршрут ведёт на «Сегодня», а не падает', () {
      expect(AppRoute.fromLegacy('что-то своё'), const AppRoute(AppSection.today));
    });
  });

  group('Холодный старт', () {
    test('книга читается без единого перехода между разделами', () async {
      // Приложение стартует уже на «Сегодня», и подгрузка данных висела
      // только на переходах: раздел, с которого всё начинается, оставался
      // единственным неинициализированным.
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: LocalStore.inMemory(),
        vault: const SecureVault(),
      );
      final controller = AppController(repository);
      addTearDown(controller.dispose);

      expect(controller.capital, isNull);
      await controller.load();
      // Дайджест в тесте не считается (сети нет) — важно, что состояние
      // капитала при этом всё равно прочитано.
      expect(controller.capital, isNotNull,
          reason: 'на «Сегодня» нечего показывать без книги');
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
