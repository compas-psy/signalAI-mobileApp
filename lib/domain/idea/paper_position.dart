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
///
/// Один вид на два источника. Сопровождение сделки переехало на сервер:
/// устройство брало свечи только у биржи напрямую, Bybit отвечает телефону
/// владельца `403 — CloudFront блокирует доступ из вашей страны`, свечей нет,
/// сверка не вызывается ни разу — и позиция замирает навсегда. Именно так
/// BTCUSDT провисел несколько дней на «взято тейков: 2 из 3». Сервер до
/// биржи доходит и работает, когда телефон выключен, поэтому источник истины
/// — он; расчёт на устройстве остаётся резервом на случай, когда движок
/// недоступен.
///
/// Карточке при этом всё равно, откуда сделка: вопрос у владельца один — что
/// сейчас открыто и чем защищено.
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

  final String symbol;
  final bool long;

  /// Заявка выставлена, но цена до неё не дошла.
  final bool pending;

  final PaperPositionStatus status;

  final double entry;

  /// Стоп, обещанный в плане. Неизменен: по нему считается R.
  final double initialStop;

  /// Стоп, который защищает позицию сейчас.
  ///
  /// Отдельно от [initialStop] потому, что на устройстве поле было одно и
  /// неизменяемое, а перенос в безубыток жил во временной переменной внутри
  /// пересчёта — то есть не существовал ни в базе, ни на экране. Владелец
  /// видел исходный стоп даже тогда, когда позиция фактически была защищена.
  final double currentStop;

  final List<double> tpPrices;
  final int tpsTaken;

  /// Когда стоп встал в безубыток. null — ещё не вставал.
  final DateTime? breakevenAt;

  final bool atBreakeven;

  /// Чем завершилась серверная paper-сделка.
  final DateTime? closedAt;
  final String outcome;
  final String closeReason;

  /// Результат в R.
  ///
  /// Что именно он означает, зависит от [resultRealized], и это не педантизм.
  /// Сервер считает **зафиксированное**: сумму по уже взятым целям, без
  /// открытого остатка. Устройство считает **плавающее**: сколько сделка
  /// стоит целиком, если закрыть её сейчас. Числа разные, а подпись «R» у
  /// них была одна — то есть карточка утверждала одно и то же о двух разных
  /// величинах, и владелец не мог знать, какую видит.
  final double? resultR;

  /// Зафиксированный результат, а не плавающий.
  final bool resultRealized;

  /// Когда сверка последний раз действительно смотрела бары.
  final DateTime? lastReconciledAt;

  /// Сколько часов сделку не с чем было сверить.
  ///
  /// Молчание источника обязано быть видно: «сверка не идёт» и «сделка
  /// спокойно живёт» выглядят на экране одинаково, и владелец справедливо
  /// считает второе, глядя на первое.
  final int? staleHours;

  final bool fromServer;

  /// Сколько тейков всего в плане.
  int get tpsTotal => tpPrices.length;

  /// Сверка молчит достаточно долго, чтобы об этом сказать.
  ///
  /// Порог в шесть часов, а не час: рабочий таймфрейм сопровождения — H1, и
  /// пропуск одного-двух баров это норма выходного дня, а не поломка.
  bool get stale => (staleHours ?? 0) >= 6;

  /// Позиция из локального журнала — резерв, когда движок недоступен.
  ///
  /// Текущего стопа у локальной сделки нет: поле объявлено `final` и никогда
  /// не двигалось. Поэтому [currentStop] равен исходному, а [breakevenAt]
  /// пуст — врать про защиту, которой нет в данных, нельзя.
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
        // Плавающий: локальная сверка переигрывает сделку целиком и знает,
        // сколько она стоит сейчас.
        resultR: trade.unrealizedR ?? trade.resultR,
        closedAt: trade.closedAt,
        outcome: trade.outcome ?? '',
      );
}
