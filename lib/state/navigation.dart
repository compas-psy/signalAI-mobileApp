/// Навигация по ТЗ v2: Today · Portfolio · Ideas · Journal · Settings.
///
/// Разделов ровно пять, и каждый отвечает на один вопрос: что делать сегодня,
/// как устроены деньги вдолгую, что происходит с идеями, чем всё закончилось,
/// как это настроено. Подразделы — пилюли в шапке, а не отдельные адреса.
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
        AppSection.today => const [],
        AppSection.portfolio =>
          const ['Пакеты', 'Сигналы', 'Ребалансировка', 'Счета'],
        AppSection.ideas => const [
            'Все',
            'Нужно решить',
            'Формируются',
            'Ждут входа',
            'Позиции открыты',
          ],
        AppSection.journal => const ['Сделки', 'Пропуски', 'Метрики'],
        // Thin-клиент не показывает справочные вкладки под видом настроек.
        // Execution mode и security gates видны в контексте риска/сделки;
        // отдельная пилюля имеет право существовать только если ею можно
        // что-то изменить или запустить.
        AppSection.settings => const [
            'Риск',
            'Подключения',
            'Стратегии',
            'Уведомления',
            'Данные',
          ],
      };

  static AppSection parse(String? raw) =>
      AppSection.values.where((s) => s.name == raw).firstOrNull ??
      AppSection.today;
}

/// Подразделы «Портфеля» (ТЗ §7).
enum PortfolioPill { packages, signals, rebalance, accounts }

/// Разрез единой воронки идей. Нулевой индекс — полный lifecycle, поэтому
/// вход в раздел больше не приводит пользователя на потенциально пустой
/// фильтр «Решения».
enum IdeasPill { all, decisions, forming, pending, open }

/// Подразделы «Журнала» (ТЗ §12).
enum JournalPill { trades, skips, metrics }

/// Полный набор исторических подразделов настроек.
///
/// Enum не ломаем ради совместимости со старым локальным экраном. Thin UI
/// использует [thinValues], где нет двух справочных вкладок mode/security.
enum SettingsPill {
  mode,
  risk,
  connections,
  strategies,
  notifications,
  data,
  security;

  static const List<SettingsPill> thinValues = [
    risk,
    connections,
    strategies,
    notifications,
    data,
  ];

  static SettingsPill thinAt(int index) =>
      thinValues[index.clamp(0, thinValues.length - 1)];
}

/// Адрес внутри приложения: раздел и выбранная пилюля.
class AppRoute {
  const AppRoute(this.section, [this.pill = 0]);

  final AppSection section;
  final int pill;

  static AppRoute fromLegacy(String legacyTab) => switch (legacyTab) {
        'ideas' || 'trading' => const AppRoute(AppSection.ideas),
        'invest' || 'capital' => const AppRoute(AppSection.portfolio),
        'trades' => const AppRoute(AppSection.journal),
        // В thin-порядке «Стратегии» — третья пилюля.
        'strategies' || 'lab' => const AppRoute(AppSection.settings, 2),
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

  bool get allowsOpening => this == RiskMode.normal || this == RiskMode.caution;
}

/// Чем закончилось действие, о котором говорит тост.
enum ToastTone {
  success,
  warning,
  failure,
}

/// Здоровье данных: на чём посчитано то, что сейчас на экране.
enum DataHealth {
  full('данные полные'),
  partial('данные неполные'),
  blind('данных нет');

  const DataHealth(this.label);

  final String label;

  bool get isDegraded => this != DataHealth.full;
}
