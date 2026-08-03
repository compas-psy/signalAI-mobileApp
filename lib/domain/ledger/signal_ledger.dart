import '../analysis/candle.dart';
import '../analysis/trade_simulator.dart';
import '../models/signal.dart';

/// Статус бумажной сделки в журнале.
enum PaperStatus {
  /// Лимитка выставлена, цена до неё не дошла.
  pending,

  /// Позиция открыта, ведётся по свечам.
  open,

  /// Сделка закрыта (стоп/тейки/горизонт) — результат зафиксирован.
  closed,

  /// Лимитка не исполнилась за отведённое время и снята.
  cancelled;

  static PaperStatus parse(String v) => PaperStatus.values.firstWhere(
        (s) => s.name == v,
        orElse: () => PaperStatus.pending,
      );
}

/// Бумажная сделка: снапшот сигнала в момент выдачи + прожитый результат.
///
/// Записывается навсегда в момент выдачи сигнала и дальше только проживается
/// вперёд по реальным свечам — уровни задним числом не редактируются. Это и
/// есть форвард-статистика стратегии, которой можно верить: в отличие от
/// бэктеста, здесь ничего нельзя подогнать.
class PaperTrade {
  PaperTrade({
    required this.id,
    required this.symbol,
    this.signalId = '',
    required this.strategyId,
    required this.long,
    required this.entry,
    required this.stopLoss,
    required this.tpPrices,
    required this.tpShares,
    required this.score,
    required this.createdAt,
    this.status = PaperStatus.pending,
    this.resultR,
    this.outcome,
    this.closedAt,
    this.unrealizedR,
    this.tpsTaken = 0,
    this.costs = TradingCosts.none,
    this.fillMargin = 0,
    this.breakevenAfterTp1 = false,
    this.stopEntry = false,
  });

  final String id;

  /// Идея, из которой сделка родилась. Пустая строка — сделка заведена
  /// руками либо пришла из источника, у которого идеи нет.
  ///
  /// Без этой ссылки журнал остаётся списком тикеров: нажать на сделку и
  /// увидеть, на чём она строилась, невозможно — а именно за этим в журнал
  /// и приходят.
  final String signalId;
  final String symbol;
  final String strategyId;
  final bool long;
  final double entry;
  final double stopLoss;
  final List<double> tpPrices;
  final List<int> tpShares;
  final int score;
  final DateTime createdAt;

  /// Издержки сделки — те же, что считает бэктест.
  ///
  /// Без них бумажная статистика мерила бы более щедрую стратегию, чем прогон,
  /// и сравнивать их было бы бессмысленно. Сохраняются вместе со сделкой:
  /// профиль комиссий может измениться, а уже прожитая сделка — нет.
  final TradingCosts costs;

  /// Шаг цены: лимитка исполняется только при проходе за уровень.
  final double fillMargin;

  /// Правило ведения на момент открытия: после TP1 стоп остатка в безубытке.
  ///
  /// Хранится в самой сделке: правило могло смениться, а уже открытая сделка
  /// обязана проживаться так, как была открыта.
  final bool breakevenAfterTp1;

  /// Вход стоп-заявкой по пробою — сторона исполнения лимитки обратная.
  final bool stopEntry;

  PaperStatus status;

  /// Итог в R — только у закрытых.
  double? resultR;

  /// Чем закончилось: «TP3», «SL», «гор.», «отм.».
  String? outcome;
  DateTime? closedAt;

  /// Текущий плавающий результат открытой позиции, R.
  double? unrealizedR;
  int tpsTaken;

  /// Прожить сделку вперёд по свечам после [createdAt].
  ///
  /// Проигрывание идёт с нуля при каждой сверке — детерминированно из
  /// исходного снапшота, без накопления инкрементального состояния,
  /// которое могло бы разъехаться.
  void reconcile(List<Candle> hourly, {int orderTtlBars = 24, int maxHoldBars = 120}) {
    if (status == PaperStatus.closed || status == PaperStatus.cancelled) return;

    final bars = [
      for (final c in hourly)
        if (c.time.isAfter(createdAt)) c,
    ];
    if (bars.isEmpty) return;

    final order = PendingOrder(
      long: long,
      entry: entry,
      stopLoss: stopLoss,
      takeProfits: [
        for (var i = 0; i < tpPrices.length; i++)
          (price: tpPrices[i], share: tpShares[i] / 100),
      ],
      expiresAt: orderTtlBars,
      costs: costs,
      fillMargin: fillMargin,
      breakevenAfterTp1: breakevenAfterTp1,
      stopEntry: stopEntry,
    );

    OpenPosition? position;
    for (var i = 0; i < bars.length; i++) {
      final bar = bars[i];
      if (position == null) {
        if (order.crossedBy(bar)) {
          position = OpenPosition(order: order, openedAt: i, maxHoldBars: maxHoldBars);
          // Свеча входа может сразу задеть стоп или тейк.
          final closed = position.onBar(bar, i);
          if (closed != null) {
            _close(closed, position, bar.time);
            return;
          }
          continue;
        }
        if (i >= orderTtlBars) {
          status = PaperStatus.cancelled;
          outcome = 'отм.';
          closedAt = bar.time;
          return;
        }
        continue;
      }

      final closed = position.onBar(bar, i);
      if (closed != null) {
        _close(closed, position, bar.time);
        return;
      }
    }

    if (position != null) {
      status = PaperStatus.open;
      unrealizedR = position.unrealizedR(bars.last.close);
      tpsTaken = position.nextTp;
    }
  }

  void _close(double result, OpenPosition position, DateTime at) {
    status = PaperStatus.closed;
    resultR = result;
    closedAt = at;
    unrealizedR = null;
    tpsTaken = position.nextTp;
    outcome = position.remainingShare <= 1e-9 && position.nextTp >= tpPrices.length
        ? 'TP${tpPrices.length}'
        : position.stoppedAtBreakeven
            ? 'TP${position.nextTp}·БУ'
            : result <= -0.999
                ? 'SL'
                : position.nextTp > 0
                    ? 'TP${position.nextTp}·гор.'
                    : result < 0
                        ? 'SL'
                        : 'гор.';
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'signal_id': signalId,
        'symbol': symbol,
        'strategy_id': strategyId,
        'long': long,
        'entry': entry,
        'stop_loss': stopLoss,
        'tp_prices': tpPrices,
        'tp_shares': tpShares,
        'score': score,
        'created_at': createdAt.toIso8601String(),
        'status': status.name,
        'result_r': resultR,
        'outcome': outcome,
        'closed_at': closedAt?.toIso8601String(),
        'unrealized_r': unrealizedR,
        'tps_taken': tpsTaken,
        'costs': costs.toJson(),
        'fill_margin': fillMargin,
        'breakeven_after_tp1': breakevenAfterTp1,
        'stop_entry': stopEntry,
      };

  factory PaperTrade.fromJson(Map<String, dynamic> j) => PaperTrade(
        id: j['id'] as String,
        // Сделки, записанные до появления ссылки, её не получат задним
        // числом: журнал только дополняется, переписывать его нельзя.
        signalId: j['signal_id'] as String? ?? '',
        symbol: j['symbol'] as String,
        strategyId: j['strategy_id'] as String? ?? 'forts',
        long: j['long'] as bool? ?? true,
        entry: (j['entry'] as num).toDouble(),
        stopLoss: (j['stop_loss'] as num).toDouble(),
        tpPrices: [for (final p in j['tp_prices'] as List<dynamic>) (p as num).toDouble()],
        tpShares: [for (final s in j['tp_shares'] as List<dynamic>) (s as num).toInt()],
        score: (j['score'] as num?)?.toInt() ?? 0,
        createdAt: DateTime.parse(j['created_at'] as String),
        status: PaperStatus.parse(j['status'] as String? ?? 'pending'),
        resultR: (j['result_r'] as num?)?.toDouble(),
        outcome: j['outcome'] as String?,
        closedAt: j['closed_at'] == null ? null : DateTime.tryParse(j['closed_at'] as String),
        unrealizedR: (j['unrealized_r'] as num?)?.toDouble(),
        tpsTaken: (j['tps_taken'] as num?)?.toInt() ?? 0,
        costs: j['costs'] == null
            ? TradingCosts.none
            : TradingCosts.fromJson(j['costs'] as Map<String, dynamic>),
        fillMargin: (j['fill_margin'] as num?)?.toDouble() ?? 0,
        // Старые записи жили без правила безубытка — оно к ним не применяется.
        breakevenAfterTp1: j['breakeven_after_tp1'] as bool? ?? false,
        stopEntry: j['stop_entry'] as bool? ?? false,
      );
}

/// Отбракованный кандидат с ценой на момент отбраковки — чтобы потом честно
/// проверить, не режут ли фильтры прибыль.
class RejectedRecord {
  RejectedRecord({
    required this.symbol,
    required this.reason,
    required this.price,
    required this.createdAt,
    this.movePercent24h,
  });

  final String symbol;
  final String reason;
  final double price;
  final DateTime createdAt;

  /// Ход цены за 24 часа после отбраковки, % (заполняется сверкой).
  double? movePercent24h;

  Map<String, dynamic> toJson() => {
        'symbol': symbol,
        'reason': reason,
        'price': price,
        'created_at': createdAt.toIso8601String(),
        'move_24h': movePercent24h,
      };

  factory RejectedRecord.fromJson(Map<String, dynamic> j) => RejectedRecord(
        symbol: j['symbol'] as String,
        reason: j['reason'] as String? ?? '',
        price: (j['price'] as num).toDouble(),
        createdAt: DateTime.parse(j['created_at'] as String),
        movePercent24h: (j['move_24h'] as num?)?.toDouble(),
      );
}

/// Вечный журнал сигналов: каждая идея записывается при выдаче и проживается
/// вперёд по реальным свечам тем же движком, что и бэктест.
class SignalLedger {
  SignalLedger({List<PaperTrade>? trades, List<RejectedRecord>? rejected})
      : trades = trades ?? [],
        rejected = rejected ?? [];

  final List<PaperTrade> trades;
  final List<RejectedRecord> rejected;

  /// Ограничители размера: журнал живёт годами, но телефон — не архив.
  static const maxTrades = 500;
  static const maxRejected = 300;

  /// Регистрация нового сигнала. Если по символу уже есть живая запись
  /// (лимитка или позиция) — новая не создаётся: это та же идея, уточнённая
  /// пересчётом, а не вторая сделка.
  void record(
    TradingSignal signal,
    DateTime now, {
    TradingCosts costs = TradingCosts.none,
    double fillMargin = 0,
    bool breakevenAfterTp1 = false,
  }) {
    final alive = trades.any((t) =>
        t.symbol == signal.symbol &&
        (t.status == PaperStatus.pending || t.status == PaperStatus.open));
    if (alive) return;

    trades.add(PaperTrade(
      id: '${signal.symbol}-${now.millisecondsSinceEpoch}',
      signalId: signal.id,
      symbol: signal.symbol,
      strategyId: signal.market.name == 'crypto' ? 'crypto' : 'forts',
      long: signal.direction.isLong,
      entry: signal.entry,
      stopLoss: signal.stopLoss,
      tpPrices: [for (final tp in signal.takeProfits) tp.price],
      tpShares: [for (final tp in signal.takeProfits) tp.sharePercent],
      score: signal.score,
      createdAt: now,
      costs: costs,
      fillMargin: fillMargin,
      breakevenAfterTp1: breakevenAfterTp1,
      stopEntry: signal.entryIsStop,
    ));
    if (trades.length > maxTrades) trades.removeRange(0, trades.length - maxTrades);
  }

  /// Сколько живёт отбраковка.
  ///
  /// Срок, а не только количество. Предел в 300 записей значил, что на
  /// редком инструменте строка висела месяцами: форвард-проверка «а что было
  /// бы, если бы взяли» считается за сутки, после чего запись — история, а
  /// не новость. Владелец видел это как «в журнале висят непонятные ошибки».
  static const rejectionTtl = Duration(days: 14);

  void recordRejection(String symbol, String reason, double price, DateTime now) {
    pruneRejections(now);
    // Одна запись на символ в сутки: отбраковка повторяется каждый час,
    // журналу нужен факт, а не эхо.
    final duplicate = rejected.any((r) =>
        r.symbol == symbol && now.difference(r.createdAt) < const Duration(hours: 24));
    if (duplicate) return;
    rejected.add(RejectedRecord(symbol: symbol, reason: reason, price: price, createdAt: now));
    if (rejected.length > maxRejected) {
      rejected.removeRange(0, rejected.length - maxRejected);
    }
  }

  /// Убрать протухшие отбраковки.
  ///
  /// Отдельным методом, потому что вызывать его нужно и при чтении: журнал
  /// может месяц пролежать без единой новой записи, и тогда сам факт записи
  /// протухшее не вычистит.
  void pruneRejections(DateTime now) {
    rejected.removeWhere((r) => now.difference(r.createdAt) > rejectionTtl);
  }

  /// Сверка всех живых записей по свежим свечам (символ → бары рабочего ТФ).
  ///
  /// [orderTtlBars] и [maxHoldBars] задаются журналом-владельцем: у свинга это
  /// часы (24/120), у дневного «Инвеста» — дни (5/60). Один журнал — один
  /// таймфрейм; смешивать их в одном экземпляре нельзя.
  void reconcile(
    Map<String, List<Candle>> hourlyBySymbol, {
    int orderTtlBars = 24,
    int maxHoldBars = 120,
  }) {
    for (final trade in trades) {
      final candles = hourlyBySymbol[trade.symbol];
      if (candles != null) {
        trade.reconcile(candles, orderTtlBars: orderTtlBars, maxHoldBars: maxHoldBars);
      }
    }
    for (final record in rejected) {
      if (record.movePercent24h != null) continue;
      final candles = hourlyBySymbol[record.symbol];
      if (candles == null || record.price <= 0) continue;
      final after = record.createdAt.add(const Duration(hours: 24));
      Candle? bar;
      for (final c in candles) {
        if (!c.time.isBefore(after)) {
          bar = c;
          break;
        }
      }
      if (bar != null) {
        record.movePercent24h = (bar.close - record.price) / record.price * 100;
      }
    }
  }

  // ── Агрегаты ──────────────────────────────────────────────────────────

  List<PaperTrade> get closed =>
      [for (final t in trades) if (t.status == PaperStatus.closed) t];

  List<PaperTrade> get openOrPending => [
        for (final t in trades)
          if (t.status == PaperStatus.open || t.status == PaperStatus.pending) t,
      ];

  double get totalR => closed.fold<double>(0, (s, t) => s + (t.resultR ?? 0));

  double get winRate {
    final done = closed;
    if (done.isEmpty) return 0;
    return done.where((t) => (t.resultR ?? 0) > 0).length / done.length * 100;
  }

  double get averageR {
    final done = closed;
    if (done.isEmpty) return 0;
    return totalR / done.length;
  }

  double? get profitFactor {
    var win = 0.0;
    var loss = 0.0;
    for (final t in closed) {
      final r = t.resultR ?? 0;
      if (r > 0) {
        win += r;
      } else {
        loss -= r;
      }
    }
    if (loss == 0) return null;
    return win / loss;
  }

  List<double> get equityCurve {
    var sum = 0.0;
    return [for (final t in closed) sum += t.resultR ?? 0];
  }

  /// Средний ход отбракованных кандидатов за 24 часа, %. null — данных нет.
  double? get rejectedAverageMove24h {
    final moves = [
      for (final r in rejected)
        if (r.movePercent24h != null) r.movePercent24h!,
    ];
    if (moves.isEmpty) return null;
    return moves.reduce((a, b) => a + b) / moves.length;
  }

  Map<String, dynamic> toJson() => {
        'trades': [for (final t in trades) t.toJson()],
        'rejected': [for (final r in rejected) r.toJson()],
      };

  factory SignalLedger.fromJson(Map<String, dynamic> j) => SignalLedger(
        trades: [
          for (final t in j['trades'] as List<dynamic>? ?? const [])
            PaperTrade.fromJson(t as Map<String, dynamic>),
        ],
        rejected: [
          for (final r in j['rejected'] as List<dynamic>? ?? const [])
            RejectedRecord.fromJson(r as Map<String, dynamic>),
        ],
      );
}
