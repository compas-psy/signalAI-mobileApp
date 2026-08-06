/// Направление сделки.
enum Direction {
  long,
  short;

  bool get isLong => this == Direction.long;

  /// Метка на бейдже карточки.
  String get label => isLong ? 'LONG' : 'SHORT';

  static Direction parse(String v) =>
      v.toLowerCase() == 'short' ? Direction.short : Direction.long;
}

/// Горизонт сигнала (ТЗ §1: INTRADAY и SWING не смешиваются в скоринге).
enum Horizon {
  intraday,
  swing;

  static Horizon parse(String v) =>
      v.toLowerCase() == 'intraday' ? Horizon.intraday : Horizon.swing;
}

/// Рынок инструмента.
enum Market {
  forts('FORTS'),
  moex('MOEX'),
  crypto('CRYPTO');

  const Market(this.label);
  final String label;

  static Market parse(String v) => Market.values.firstWhere(
        (m) => m.label.toLowerCase() == v.toLowerCase(),
        orElse: () => Market.forts,
      );
}

/// Статус-машина сигнала (ТЗ §5.4).
///
/// PROPOSED → PUSHED → CONFIRMED → WORKING → OPEN → CLOSED,
/// либо ветки INVALIDATED / EXPIRED / REJECTED.
enum SignalStatus {
  /// Контекст есть, но сервер ещё не подтвердил триггер. Никакого действия.
  watch,
  proposed,
  pushed,
  confirmed,
  working,
  open,
  closed,
  invalidated,
  expired,
  rejected;

  /// Подтверждение доступно только для «живого» сигнала (ТЗ §1: просроченный
  /// или инвалидированный подтвердить нельзя — кнопка гаснет).
  bool get canConfirm => this == SignalStatus.proposed || this == SignalStatus.pushed;

  /// Сигнал уже принят в работу: выставлен ордер или открыта позиция.
  bool get isWorking =>
      this == SignalStatus.confirmed || this == SignalStatus.working || this == SignalStatus.open;

  /// Подпись статуса на карточке идеи.
  String get label =>
      this == SignalStatus.watch ? 'Наблюдать' : (isWorking ? 'В работе' : 'Новая');

  static SignalStatus parse(String v) => SignalStatus.values.firstWhere(
        (s) => s.name == v.toLowerCase(),
        orElse: () => SignalStatus.proposed,
      );
}

/// Влияние события на идею (ТЗ §3, событийный стоп-лист).
enum EventImpact {
  low,
  mid,
  high;

  static EventImpact parse(String v) => EventImpact.values.firstWhere(
        (i) => i.name == v.toLowerCase(),
        orElse: () => EventImpact.low,
      );
}

/// Режим стратегии (ТЗ §1: paper обязателен перед live).
enum StrategyMode {
  paper,
  live;

  static StrategyMode parse(String v) =>
      v.toLowerCase() == 'live' ? StrategyMode.live : StrategyMode.paper;
}

/// Семантический тон значения — цвет подставляет UI, а не модель.
enum Tone { positive, negative, neutral, accent }
