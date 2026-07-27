/// Навигация v3: пять разделов, подразделы — пилюли в шапке.
///
/// Почему подразделы не отдельные экраны: пять разделов по пять экранов —
/// это двадцать пять адресов на одного пользователя. Каждый лишний уровень
/// стоит трёх секунд поиска утром, когда нужно решение, а не путешествие по
/// меню. Глубина фиксирована: раздел → пилюля → разбор конкретного объекта.
library;

/// Верхнеуровневый раздел.
enum AppSection {
  today('Сегодня'),
  capital('Капитал'),
  trading('Торговля'),
  lab('Лаборатория'),
  control('Контроль');

  const AppSection(this.title);

  final String title;

  /// Подписи пилюль. Пусто — у раздела нет подразделов.
  List<String> get pills => switch (this) {
        AppSection.today => const [],
        AppSection.capital => const ['Обзор', 'Счета', 'Пакеты', 'Книга', 'Аналитика'],
        AppSection.trading => const ['Идеи', 'Позиции', 'Опционы', 'Журнал'],
        AppSection.lab => const ['Стратегии', 'Скринер РФ'],
        AppSection.control =>
          const ['Риск и лимиты', 'Интеграции', 'Уведомления', 'Безопасность'],
      };

  static AppSection parse(String? raw) => AppSection.values
      .where((s) => s.name == raw)
      .firstOrNull ??
      AppSection.today;
}

/// Подразделы «Капитала» — по индексу пилюли.
enum CapitalPill { overview, accounts, packages, book, analytics }

/// Подразделы «Торговли».
enum TradingPill { ideas, positions, options, journal }

/// Подразделы «Лаборатории».
enum LabPill { strategies, screener }

/// Подразделы «Контроля».
enum ControlPill { risk, integrations, notifications, security }

/// Адрес внутри приложения: раздел и выбранная пилюля.
class AppRoute {
  const AppRoute(this.section, [this.pill = 0]);

  final AppSection section;

  /// Индекс пилюли внутри раздела.
  final int pill;

  /// Куда ведут маршруты версии 2 — чтобы сохранённое состояние и старые
  /// ссылки не упирались в пустоту.
  ///
  /// Идеи → Торговля·Идеи, Инвест → Лаборатория·Скринер, Сделки →
  /// Торговля·Позиции, Стратегии → Лаборатория, Настройки → Контроль.
  static AppRoute fromLegacy(String legacyTab) => switch (legacyTab) {
        'ideas' => const AppRoute(AppSection.trading, 0),
        'invest' => const AppRoute(AppSection.lab, 1),
        'trades' => const AppRoute(AppSection.trading, 1),
        'strategies' => const AppRoute(AppSection.lab, 0),
        'settings' => const AppRoute(AppSection.control, 0),
        _ => const AppRoute(AppSection.today),
      };

  AppRoute withPill(int index) => AppRoute(section, index);

  @override
  bool operator ==(Object other) =>
      other is AppRoute && other.section == section && other.pill == pill;

  @override
  int get hashCode => Object.hash(section, pill);
}

/// Режим риск-движка. Индикатор в шапке, а не переключатель: режим назначает
/// движок по состоянию капитала, руками ставится только аварийная остановка.
enum RiskMode {
  normal('NORMAL', 'лимиты соблюдены'),
  caution('CAUTION', 'объём новых сделок урезан'),
  reduceOnly('REDUCE ONLY', 'только сокращение позиций'),
  killSwitch('KILL SWITCH', 'новые заявки запрещены');

  const RiskMode(this.label, this.hint);

  final String label;
  final String hint;

  /// Можно ли открывать новое.
  bool get allowsOpening => this == RiskMode.normal || this == RiskMode.caution;
}
