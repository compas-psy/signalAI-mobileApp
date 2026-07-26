import 'dart:math' as math;

import 'candle.dart';
import 'instrument_spec.dart';
import 'screener.dart';

/// История одного инструмента для бэктеста.
class InstrumentHistory {
  const InstrumentHistory({
    required this.spec,
    required this.hourly,
    required this.daily,
  });

  final InstrumentSpec spec;
  final List<Candle> hourly;
  final List<Candle> daily;
}

/// Закрытая сделка прогона: результат в R и символ.
class BacktestTrade {
  const BacktestTrade({required this.symbol, required this.resultR});

  final String symbol;
  final double resultR;
}

/// Итог прогона: сделки и агрегаты.
class BacktestSummary {
  const BacktestSummary({
    required this.trades,
    required this.days,
    required this.instruments,
  });

  final List<BacktestTrade> trades;
  final int days;
  final int instruments;

  int get count => trades.length;

  double get winRate {
    if (trades.isEmpty) return 0;
    return trades.where((t) => t.resultR > 0).length / trades.length * 100;
  }

  double get averageR {
    if (trades.isEmpty) return 0;
    return trades.fold<double>(0, (s, t) => s + t.resultR) / trades.length;
  }

  /// Профит-фактор: валовая прибыль к валовому убытку. null — убытков не было.
  double? get profitFactor {
    var win = 0.0;
    var loss = 0.0;
    for (final t in trades) {
      if (t.resultR > 0) {
        win += t.resultR;
      } else {
        loss -= t.resultR;
      }
    }
    if (loss == 0) return null;
    return win / loss;
  }

  /// Кривая эквити в R, накопительно по закрытым сделкам.
  List<double> get equityCurve {
    var sum = 0.0;
    return [for (final t in trades) sum += t.resultR];
  }
}

/// Walk-forward бэктест того же скринера, что выдаёт идеи вживую.
///
/// Честность прогона важнее красивой цифры, поэтому правила пессимистичные и
/// написаны явно:
///  * скринер видит только свечи до текущего момента — заглядывания в будущее
///    нет по построению;
///  * режим рынка в прогоне нейтральный: восстанавливать исторический режим
///    индекса на каждый час — отдельная задача, и молча подменять его текущим
///    было бы обманом;
///  * лимитка живёт [orderTtlBars] часов; если цена не дошла — идея отменяется;
///  * если свеча задевает и стоп, и тейк, засчитывается стоп (худший случай);
///  * комиссия и проскальзывание не моделируются — это тоже написано в итоге.
class Backtester {
  const Backtester({
    this.screener = const Screener(),
    this.evaluateEveryBars = 4,
    this.windowBars = 240,
    this.orderTtlBars = 24,
    this.maxHoldBars = 120,
    this.cooldownBars = 12,
  });

  final Screener screener;

  /// Как часто скринер ищет новый сетап (в барах часовика).
  final int evaluateEveryBars;

  /// Сколько последних баров видит скринер при каждом прогоне.
  final int windowBars;

  /// Время жизни лимитного ордера.
  final int orderTtlBars;

  /// Максимальное удержание позиции (свинг 1–5 дней ≈ 120 часов).
  final int maxHoldBars;

  /// Пауза после закрытия сделки перед поиском следующего сетапа.
  final int cooldownBars;

  /// Прогон по всем инструментам. [onProgress] — человекочитаемые стадии.
  Future<BacktestSummary> run(
    List<InstrumentHistory> histories, {
    void Function(String stage)? onProgress,
  }) async {
    final trades = <BacktestTrade>[];
    var maxDays = 0;

    for (final history in histories) {
      onProgress?.call('Прогон ${history.spec.symbol}…');
      // Отдаём кадр интерфейсу между инструментами: расчёт идёт в UI-изоляте.
      await Future<void>.delayed(Duration.zero);
      trades.addAll(_runInstrument(history));
      if (history.hourly.isNotEmpty) {
        final span = history.hourly.last.time.difference(history.hourly.first.time).inDays;
        maxDays = math.max(maxDays, span);
      }
    }

    // Хронология сделок между инструментами здесь не восстанавливается —
    // для агрегатов (WR, PF, средняя R) порядок не важен.
    return BacktestSummary(
      trades: trades,
      days: maxDays,
      instruments: histories.length,
    );
  }

  List<BacktestTrade> _runInstrument(InstrumentHistory history) {
    final hourly = history.hourly;
    final daily = history.daily;
    final trades = <BacktestTrade>[];
    if (hourly.length < 80 || daily.length < 40) return trades;

    _PendingOrder? pending;
    _OpenPosition? open;
    var cooldownUntil = 0;
    var dailyPointer = 0;

    for (var i = 60; i < hourly.length; i++) {
      final bar = hourly[i];

      // Сколько дневных свечей уже закрылось к началу этого часа.
      while (dailyPointer < daily.length &&
          !daily[dailyPointer].time.isAfter(bar.time.subtract(const Duration(hours: 24)))) {
        dailyPointer++;
      }

      // 1. Управление открытой позицией — каждый бар, без пропусков.
      if (open != null) {
        final closed = open.onBar(bar, i);
        if (closed != null) {
          trades.add(BacktestTrade(symbol: history.spec.symbol, resultR: closed));
          open = null;
          cooldownUntil = i + cooldownBars;
        }
        continue;
      }

      // 2. Ожидающая лимитка: исполнение или отмена по TTL.
      if (pending != null) {
        if (pending.crossedBy(bar)) {
          open = _OpenPosition(order: pending, openedAt: i, maxHoldBars: maxHoldBars);
        } else if (i >= pending.expiresAt) {
          pending = null;
        }
        if (open != null) {
          pending = null;
          continue;
        }
        if (pending != null) continue;
      }

      // 3. Поиск нового сетапа — периодически и не раньше конца паузы.
      if (i < cooldownUntil || i % evaluateEveryBars != 0) continue;
      if (dailyPointer < 30) continue;

      final windowStart = math.max(0, i + 1 - windowBars);
      final result = screener.evaluate(
        InstrumentInput(
          spec: history.spec,
          hourly: hourly.sublist(windowStart, i + 1),
          daily: daily.sublist(0, dailyPointer),
          lastPrice: bar.close,
          changePercentLabel: '0,00%',
          changeUp: true,
        ),
        MarketRegime.unknown,
        now: bar.time,
      );
      if (result == null) continue;

      final signal = result.signal;
      pending = _PendingOrder(
        long: signal.direction.isLong,
        entry: signal.entry,
        stopLoss: signal.stopLoss,
        takeProfits: [
          for (final tp in signal.takeProfits) (price: tp.price, share: tp.sharePercent / 100),
        ],
        expiresAt: i + orderTtlBars,
      );
    }

    return trades;
  }
}

class _PendingOrder {
  _PendingOrder({
    required this.long,
    required this.entry,
    required this.stopLoss,
    required this.takeProfits,
    required this.expiresAt,
  });

  final bool long;
  final double entry;
  final double stopLoss;
  final List<({double price, double share})> takeProfits;
  final int expiresAt;

  double get risk => (entry - stopLoss).abs();

  /// Дошла ли цена бара до лимитки.
  bool crossedBy(Candle bar) => long ? bar.low <= entry : bar.high >= entry;
}

class _OpenPosition {
  _OpenPosition({
    required this.order,
    required this.openedAt,
    required this.maxHoldBars,
  }) : remainingShare = 1;

  final _PendingOrder order;
  final int openedAt;
  final int maxHoldBars;

  double remainingShare;
  double realizedR = 0;
  int nextTp = 0;

  /// Обработка бара. Возвращает итог сделки в R, когда позиция закрыта.
  double? onBar(Candle bar, int index) {
    final risk = order.risk;
    final long = order.long;

    // Худший случай первым: если бар задел и стоп, и тейк — считаем стоп.
    final stopHit = long ? bar.low <= order.stopLoss : bar.high >= order.stopLoss;
    if (stopHit) {
      realizedR -= remainingShare;
      remainingShare = 0;
      return realizedR;
    }

    while (nextTp < order.takeProfits.length) {
      final tp = order.takeProfits[nextTp];
      final hit = long ? bar.high >= tp.price : bar.low <= tp.price;
      if (!hit) break;
      final r = (tp.price - order.entry).abs() / risk;
      realizedR += tp.share * r;
      remainingShare -= tp.share;
      nextTp++;
    }
    if (nextTp >= order.takeProfits.length || remainingShare <= 1e-9) {
      return realizedR;
    }

    // Горизонт свинга вышел — закрываем остаток по цене закрытия бара.
    if (index - openedAt >= maxHoldBars) {
      final r = (long ? bar.close - order.entry : order.entry - bar.close) / risk;
      realizedR += remainingShare * r;
      remainingShare = 0;
      return realizedR;
    }
    return null;
  }
}
