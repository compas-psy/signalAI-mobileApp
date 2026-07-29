/// Жизненный цикл торговой идеи (ТЗ §8).
///
/// Событийная машина состояний: идея не «появляется и висит», а проходит
/// путь, на каждом шаге которого разрешено ровно одно множество действий.
/// Разрешённые переходы заданы здесь, а не в экранах: иначе однажды кнопка
/// на одном из экранов позволит подтвердить истёкший сигнал.
enum IdeaState {
  /// Есть контекст, нет триггера. Только наблюдаем.
  watch('Watch', 'Есть контекст, нет триггера'),

  /// Зона известна, не хватает подтверждения.
  ready('Ready', 'Зона известна, ждём подтверждения'),

  /// Все условия выполнены, сигнал живёт ограниченное время.
  triggered('Triggered', 'Условия выполнены, сигнал живёт ограниченное время'),

  /// План подтверждён, заявки сопровождаются.
  active('Active', 'План подтверждён, заявки сопровождаются'),

  /// Сделка завершена.
  closed('Closed', 'Сделка завершена'),

  /// Владелец пропустил идею и указал причину.
  skipped('Skipped', 'Пропущена владельцем'),

  /// Срок сигнала истёк — исполнять нельзя.
  expired('Expired', 'Срок сигнала истёк'),

  /// Выполнено kill-условие — исполнять нельзя.
  invalidated('Invalidated', 'Замысел сломан kill-условием');

  const IdeaState(this.label, this.meaning);

  final String label;
  final String meaning;

  /// Можно ли подтвердить план в этом состоянии.
  ///
  /// Только Triggered. ТЗ §8: в Ready «не хватает подтверждения», а Expired и
  /// Invalidated прямо помечены как «нельзя исполнить».
  bool get canConfirm => this == IdeaState.triggered;

  /// Можно ли пропустить с указанием причины.
  bool get canSkip => this == IdeaState.triggered || this == IdeaState.ready;

  /// Идея завершена и уходит в журнал.
  bool get isTerminal =>
      this == IdeaState.closed ||
      this == IdeaState.skipped ||
      this == IdeaState.expired ||
      this == IdeaState.invalidated;

  /// Живая идея, которая требует внимания сегодня.
  bool get needsAttention =>
      this == IdeaState.ready || this == IdeaState.triggered;

  /// Разрешённые переходы (ТЗ §8, колонка «Переход»).
  Set<IdeaState> get allowedNext => switch (this) {
        IdeaState.watch => {IdeaState.ready, IdeaState.expired, IdeaState.invalidated},
        IdeaState.ready => {
            IdeaState.triggered,
            IdeaState.watch,
            IdeaState.skipped,
            IdeaState.expired,
            IdeaState.invalidated,
          },
        IdeaState.triggered => {
            IdeaState.active,
            IdeaState.skipped,
            IdeaState.expired,
            IdeaState.invalidated,
          },
        IdeaState.active => {IdeaState.closed},
        // Терминальные состояния никуда не ведут: журнал неизменяем.
        IdeaState.closed ||
        IdeaState.skipped ||
        IdeaState.expired ||
        IdeaState.invalidated =>
          const {},
      };

  bool canMoveTo(IdeaState next) => allowedNext.contains(next);

  static IdeaState parse(String? value) => IdeaState.values.firstWhere(
        (s) => s.name == value,
        orElse: () => IdeaState.watch,
      );
}

/// Причина пропуска идеи (ТЗ, приложение A).
///
/// Справочник закрытый: свободный текст рядом, но не вместо кода. Иначе
/// разрезы журнала по причинам пропуска посчитать нельзя.
enum SkipReason {
  notSeen('NOT_SEEN', 'Не увидел вовремя'),
  noTrust('NO_TRUST', 'Не поверил в идею'),
  riskLimit('RISK_LIMIT', 'Лимит риска'),
  lateEntry('LATE_ENTRY', 'Поздний вход'),
  technical('TECHNICAL', 'Техническая проблема'),
  psychological('PSYCHOLOGICAL', 'Психологическая причина'),
  other('OTHER', 'Другое');

  const SkipReason(this.code, this.label);

  final String code;
  final String label;

  /// Требует ли причина комментария. «Другое» без пояснения бесполезно.
  bool get needsComment => this == SkipReason.other;

  static SkipReason parse(String? code) => SkipReason.values.firstWhere(
        (r) => r.code == code,
        orElse: () => SkipReason.other,
      );
}

/// Флаг качества рыночных данных (ТЗ, приложение B).
///
/// Показывается рядом с числом, а не вместо него: ТЗ §27 требует, чтобы
/// stale и degraded были видны и не маскировались под live.
enum DataQualityFlag {
  stale('STALE', 'данные устарели'),
  gap('GAP', 'пропуск в ряду'),
  outlier('OUTLIER', 'выброс'),
  sourceConflict('SOURCE_CONFLICT', 'источники расходятся'),
  partialBar('PARTIAL_BAR', 'бар не закрыт'),
  sessionMismatch('SESSION_MISMATCH', 'сессии не совпадают'),
  oiUnavailable('OI_UNAVAILABLE', 'открытый интерес недоступен'),
  lowLiquidity('LOW_LIQUIDITY', 'низкая ликвидность'),
  corporateActionUnadjusted('CORPORATE_ACTION_UNADJUSTED', 'без корпоративных событий');

  const DataQualityFlag(this.code, this.label);

  final String code;
  final String label;
}
