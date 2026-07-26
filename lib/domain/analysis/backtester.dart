import 'dart:math' as math;

import 'candle.dart';
import 'instrument_spec.dart';
import 'screener.dart';
import 'trade_simulator.dart';

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
  const BacktestTrade({
    required this.symbol,
    required this.resultR,
    this.costR = 0,
    this.components = const [],
  });

  final String symbol;

  /// Итог сделки в R **после** издержек.
  final double resultR;

  /// Сколько R из результата съели комиссия, проскальзывание и фандинг.
  final double costR;

  /// Компоненты оценки, с которыми сделка была открыта.
  ///
  /// Нужны аудиту факторов: он агрегирует их по уже сыгранным сделкам, и
  /// поэтому меряет ровно ту стратегию, которая торговалась, а не отдельную
  /// свою модель.
  final List<ScoreComponent> components;
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

  /// Средние издержки на сделку, R. Показываются отдельно, чтобы было видно,
  /// сколько эджа уходит бирже.
  double get averageCostR {
    if (trades.isEmpty) return 0;
    return trades.fold<double>(0, (s, t) => s + t.costR) / trades.length;
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
///  * режим рынка берётся историческим — из [RegimeTimeline], собранной по
///    дневкам якорей на момент каждого бара;
///  * лимитка живёт [orderTtlBars] часов; если цена не дошла — идея отменяется;
///  * если свеча задевает и стоп, и тейк, засчитывается стоп (худший случай);
///  * комиссия, проскальзывание и фандинг вычитаются из результата —
///    правила и цифры в [TradingCosts].
class Backtester {
  const Backtester({
    this.screener = const Screener(),
    // Каждый бар — тот же каденс, что у живого контура (пересчёт по закрытию
    // часа). Прогон обязан торговать ту же стратегию, что и телефон.
    this.evaluateEveryBars = 1,
    this.windowBars = 240,
    this.orderTtlBars = 24,
    this.maxHoldBars = 120,
    this.cooldownBars = 12,
    this.costs = defaultCostsFor,
  });

  final Screener screener;

  /// Издержки по инструменту. Подменяется на [noCosts] в тестах механики.
  final TradingCosts Function(InstrumentSpec spec) costs;

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
  ///
  /// [regime] — исторический режим рынка. Без него прогон торгует нейтральный
  /// режим, и это честно отражается в подписи результата.
  Future<BacktestSummary> run(
    List<InstrumentHistory> histories, {
    void Function(String stage)? onProgress,
    RegimeTimeline? regime,
  }) async {
    final trades = <BacktestTrade>[];
    var maxDays = 0;

    for (final history in histories) {
      onProgress?.call('Прогон ${history.spec.symbol}…');
      // Отдаём кадр интерфейсу между инструментами: расчёт идёт в UI-изоляте.
      await Future<void>.delayed(Duration.zero);
      trades.addAll(_runInstrument(history, regime));
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

  List<BacktestTrade> _runInstrument(InstrumentHistory history, RegimeTimeline? regime) {
    final hourly = history.hourly;
    final daily = history.daily;
    final trades = <BacktestTrade>[];
    if (hourly.length < 80 || daily.length < 40) return trades;

    final tradeCosts = costs(history.spec);
    final fillMargin = history.spec.tick;

    PendingOrder? pending;
    OpenPosition? open;
    // Компоненты оценки текущей идеи — переезжают в закрытую сделку.
    List<ScoreComponent> pendingComponents = const [];
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
          trades.add(BacktestTrade(
            symbol: history.spec.symbol,
            resultR: closed,
            costR: open.costR,
            components: pendingComponents,
          ));
          open = null;
          pendingComponents = const [];
          cooldownUntil = i + cooldownBars;
        }
        continue;
      }

      // 2. Ожидающая лимитка: исполнение или отмена по TTL.
      if (pending != null) {
        if (pending.crossedBy(bar)) {
          open = OpenPosition(order: pending, openedAt: i, maxHoldBars: maxHoldBars);
        } else if (i >= pending.expiresAt) {
          pending = null;
          pendingComponents = const [];
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
        regime?.at(bar.time) ?? MarketRegime.unknown,
        now: bar.time,
      );
      if (result == null) continue;

      final signal = result.signal;
      pendingComponents = result.components;
      pending = PendingOrder(
        long: signal.direction.isLong,
        entry: signal.entry,
        stopLoss: signal.stopLoss,
        takeProfits: [
          for (final tp in signal.takeProfits) (price: tp.price, share: tp.sharePercent / 100),
        ],
        expiresAt: i + orderTtlBars,
        costs: tradeCosts,
        fillMargin: fillMargin,
      );
    }

    return trades;
  }
}
