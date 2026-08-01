/// Навигация по ТЗ v2: Today · Portfolio · Ideas · Journal · Settings.
///
/// Разделов ровно пять, и каждый отвечает на один вопрос: что делать сегодня,
/// как устроены деньги вдолгую, что происходит с идеями, чем всё закончилось,
/// как это настроено. Подразделы — пилюли в шапке, а не отдельные адреса:
/// пять разделов по пять экранов дали бы двадцать пять мест, где утром надо
/// искать решение.
library;

/// Верхнеуровневый раздел (ТЗ §6–§13).
enum AppSection {
  today('Сегодня'),
  portfolio('Портфель'),
  ideas('Идеи'),
  journal('Журнал'),
  settings('Настройки');

  const AppSection(this.title);

  final String title;

  /// Подписи пилюль. Пусто — у раздела нет подразделов.
  List<String> get pills => switch (this) {
        // ТЗ §6.1: Today не дублирует терминал, поэтому и делить нечего.
        AppSection.today => const [],
        // «Сигналы» — исследовательский контур ранних сигналов, а не идеи.
        // Место здесь, а не в «Идеях», потому что вопрос другой: идея
        // говорит, когда входить, гипотеза — что вообще стоит держать на
        // горизонте года-двух. У неё нет ни зоны входа, ни стопа, ни
        // объёма, и исполнять её нечем; она кормит собой пакеты и разговор
        // о ребалансировке, а не кнопку подтверждения.
        AppSection.portfolio =>
          const ['Пакеты', 'Сигналы', 'Ребалансировка', 'Счета'],
        // ТЗ §8.2: фильтр по состоянию — главный разрез ленты идей.
        AppSection.ideas => const ['Решения', 'Наблюдение', 'В работе', 'Все'],
        AppSection.journal => const ['Сделки', 'Пропуски', 'Метрики'],
        AppSection.settings => const [
            'Режим',
            'Риск',
            'Подключения',
            'Стратегии',
            'Уведомления',
            'Данные',
            'Безопасность',
          ],
      };

  static AppSection parse(String? raw) =>
      AppSection.values.where((s) => s.name == raw).firstOrNull ??
      AppSection.today;
}

/// Подразделы «Портфеля» (ТЗ §7).
enum PortfolioPill { packages, signals, rebalance, accounts }

/// Разрез ленты идей (ТЗ §8.2).
enum IdeasPill { decisions, watch, active, all }

/// Подразделы «Журнала» (ТЗ §12).
enum JournalPill { trades, skips, metrics }

/// Подразделы «Настроек» (ТЗ §13).
enum SettingsPill {
  mode,
  risk,
  connections,
  strategies,
  notifications,
  data,
  security,
}

/// Адрес внутри приложения: раздел и выбранная пилюля.
class AppRoute {
  const AppRoute(this.section, [this.pill = 0]);

  final AppSection section;

  /// Индекс пилюли внутри раздела.
  final int pill;

  /// Куда ведут сохранённые адреса прежних версий.
  ///
  /// Разделы «Капитал», «Торговля», «Лаборатория» и «Контроль» упразднены
  /// вместе с операционной системой капитала. Сохранённое состояние не должно
  /// упираться в пустоту: приложение открывается там, где смысл ближе всего.
  static AppRoute fromLegacy(String legacyTab) => switch (legacyTab) {
        'ideas' || 'trading' => const AppRoute(AppSection.ideas),
        'invest' || 'capital' => const AppRoute(AppSection.portfolio),
        'trades' => const AppRoute(AppSection.journal),
        'strategies' || 'lab' =>
          AppRoute(AppSection.settings, SettingsPill.strategies.index),
        'settings' || 'control' => const AppRoute(AppSection.settings),
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
/// движок по состоянию лимитов, руками ставится только аварийная остановка.
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
