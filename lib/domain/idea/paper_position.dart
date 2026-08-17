import '../ledger/signal_ledger.dart' show PaperStatus, PaperTrade;

/// Полное состояние сделки в серверном paper-ledger.
///
/// Одного булевого `pending` недостаточно фоновому thin-клиенту: исчезновение
/// сделки из live-only выдачи не доказывает закрытие. CLOSED/CANCELLED должны
/// приехать явным серверным состоянием.
enum PaperPositionStatus {
  pending,
  open,
  closed,
  cancelled;

  bool get live => this == pending || this == open;

  static PaperPositionStatus parse(String? raw) => switch (raw?.toUpperCase()) {
        'OPEN' => PaperPositionStatus.open,
        'CLOSED' => PaperPositionStatus.closed,
        'CANCELLED' => PaperPositionStatus.cancelled,
        _ => PaperPositionStatus.pending,
      };
}

/// Открытая бумажная позиция так, как её показывает экран.
class PaperPosition {
  const PaperPosition({
    required this.id,
    required this.symbol,
    required this.long,
    required this.pending,
    required this.entry,
    required this.initialStop,
    required this.currentStop,
    required this.tpPrices,
    required this.tpsTaken,
    this.ideaId = '',
    this.instrumentId = '',
    this.breakevenAt,
    this.resultR,
    this.resultRealized = false,
    this.lastReconciledAt,
    this.staleHours,
    this.fromServer = false,
    PaperPositionStatus? status,
    bool? atBreakeven,
    this.closedAt,
    this.outcome = '',
    this.closeReason = '',
  })  : status = status ??
            (pending ? PaperPositionStatus.pending : PaperPositionStatus.open),
        atBreakeven = atBreakeven ?? (breakevenAt != null);

  final String id;

  /// Идея, из которой сделка родилась. Пустая строка — сделки без идеи:
  /// журнал ведёт и скринер на устройстве, у которого сигналы свои.
  final String ideaId;

  /// Канонический серверный идентификатор инструмента.
  ///
  /// Сделка живёт дольше карточки идеи, поэтому площадку нельзя восстанавливать
  /// поиском текущей идеи: после её ухода из today-feed пропадал Sandbox audit
  /// у всё ещё открытого FORTS-трейда.
  final String instrumentId;

  final String symbol;
  final bool long;
  final bool pending;
  final PaperPositionStatus status;

  /// Источник истины для venue-sensitive UI. Тикер намеренно не угадываем:
  /// одинаковые коды могут существовать на разных площадках.
  bool get isForts => instrumentId.toUpperCase().startsWith('MOEX:FUT:');

  final double entry;
  final double initialStop;

  /// Стоп, который защищает позицию сейчас.
  final double currentStop;

  final List<double> tpPrices;
  final int tpsTaken;

  /// TP3 уже пройден, но сервер намеренно оставил сделку OPEN: последняя
  /// подписанная доля позиции стала runner-ом и ведётся trailing engine.
  ///
  /// Это вывод из серверного lifecycle, а не второй расчёт на телефоне.
  /// Legacy hard-TP3 после третьей цели уже CLOSED и сюда не попадёт.
  bool get runnerActive =>
      fromServer &&
      status == PaperPositionStatus.open &&
      tpPrices.length >= 3 &&
      tpsTaken >= tpPrices.length;

  final DateTime? breakevenAt;
  final bool atBreakeven;

  final DateTime? closedAt;
  final String outcome;
  final String closeReason;

  /// Результат в R. Серверный — зафиксированный, локальный — плавающий.
  final double? resultR;
  final bool resultRealized;

  final DateTime? lastReconciledAt;
  final int? staleHours;
  final bool fromServer;

  int get tpsTotal => tpPrices.length;

  bool get stale => (staleHours ?? 0) >= 6;

  factory PaperPosition.fromLedger(PaperTrade trade) => PaperPosition(
        id: trade.id,
        ideaId: trade.signalId,
        symbol: trade.symbol,
        long: trade.long,
        pending: trade.status == PaperStatus.pending,
        entry: trade.entry,
        initialStop: trade.stopLoss,
        currentStop: trade.stopLoss,
        tpPrices: trade.tpPrices,
        tpsTaken: trade.tpsTaken,
        status: switch (trade.status) {
          PaperStatus.pending => PaperPositionStatus.pending,
          PaperStatus.open => PaperPositionStatus.open,
          PaperStatus.closed => PaperPositionStatus.closed,
          PaperStatus.cancelled => PaperPositionStatus.cancelled,
        },
        resultR: trade.unrealizedR ?? trade.resultR,
        closedAt: trade.closedAt,
        outcome: trade.outcome ?? '',
      );
}
