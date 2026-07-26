import '../analysis/instrument_spec.dart';
import '../analysis/screener.dart';
import '../enums.dart';
import '../ledger/signal_ledger.dart';
import '../models/settings.dart';
import '../models/signal.dart';

/// Границы, за которые торговля не выходит.
///
/// Значения — не «настройка по вкусу», а стандартная защита свинг-портфеля:
/// ограничить одновременную экспозицию, не досиживать плохой день до конца и
/// не лезть объёмом в неликвид. Всё проверяемо и всё видно пользователю.
class RiskLimits {
  const RiskLimits({
    this.maxConcurrent = 3,
    this.dailyLossLimitR = 2.0,
    this.stopsBeforePause = 2,
    this.pauseAfterStops = const Duration(hours: 24),
    this.maxTurnoverShare = 0.005,
  });

  /// Сколько сделок может быть в работе одновременно (лимитки считаются).
  final int maxConcurrent;

  /// Дневной лимит потерь в R. Достигнут — на сегодня торговля закрыта.
  final int stopsBeforePause;
  final double dailyLossLimitR;

  /// Пауза после серии стопов подряд.
  final Duration pauseAfterStops;

  /// Доля дневного оборота инструмента, которую нам разрешено занимать.
  final double maxTurnoverShare;
}

/// Решение по кандидату: пропустить или отклонить с человеческой причиной.
class RiskDecision {
  const RiskDecision.allow()
      : allowed = true,
        reason = '';
  const RiskDecision.reject(this.reason) : allowed = false;

  final bool allowed;
  final String reason;
}

/// Живое состояние лимитов — для карточки «Риск-лимиты».
class RiskState {
  const RiskState({
    required this.open,
    required this.maxConcurrent,
    required this.dayResultR,
    required this.dailyLossLimitR,
    required this.pausedUntil,
  });

  final int open;
  final int maxConcurrent;

  /// Результат закрытых сделок за сегодня, R.
  final double dayResultR;
  final double dailyLossLimitR;

  /// До какого момента действует пауза после серии стопов. null — паузы нет.
  final DateTime? pausedUntil;
}

/// Исполняемые риск-правила: не декларация, а гейт перед публикацией идеи.
///
/// Гейты применяются после скринера. Каждый отказ уходит в журнал отбраковок
/// и через сутки получает проверку «а что было бы, если бы взяли» — риск-движок
/// обязан отчитываться о своей цене, а не прятать её.
class RiskEngine {
  const RiskEngine({this.limits = const RiskLimits()});

  final RiskLimits limits;

  /// Проверка кандидата. [dailyTurnover] — дневной оборот инструмента в
  /// рублях; null означает «оборот неизвестен», и лимит ликвидности не
  /// применяется (выдумывать ограничение из воздуха нельзя).
  RiskDecision check({
    required InstrumentSpec spec,
    required TradingSignal signal,
    required SignalLedger ledger,
    required RiskProfile risk,
    required DateTime now,
    double? dailyTurnover,
  }) {
    final live = ledger.openOrPending;

    // Идея по символу, который уже в работе, — это та же сделка, уточнённая
    // пересчётом, а не вторая. Гейты к ней не применяются: позиция открыта,
    // и запретить её задним числом нельзя.
    if (live.any((t) => t.symbol == signal.symbol)) return const RiskDecision.allow();

    // 1. Одновременная экспозиция.
    if (live.length >= limits.maxConcurrent) {
      return RiskDecision.reject(
        'уже ${live.length} позиций в работе из ${limits.maxConcurrent}',
      );
    }

    // 2. Одна идея на группу коррелированных инструментов: три лонга по
    // валютным фьючерсам — это одна ставка тройным размером.
    final group = correlationGroup(spec);
    for (final trade in live) {
      final market = trade.strategyId == 'crypto' ? Market.crypto : Market.forts;
      if (correlationGroupFor(trade.symbol, market) == group) {
        return RiskDecision.reject('в группе «$group» уже открыт ${trade.symbol}');
      }
    }

    // 3. Дневной лимит потерь.
    final dayR = dayResultR(ledger, now);
    if (dayR <= -limits.dailyLossLimitR) {
      return RiskDecision.reject(
        'дневной лимит потерь исчерпан (${_r(dayR)}R из −${_r(limits.dailyLossLimitR)}R)',
      );
    }

    // 4. Пауза после серии стопов.
    final paused = pausedUntil(ledger, now);
    if (paused != null) {
      return RiskDecision.reject(
        '${limits.stopsBeforePause} стопа подряд — пауза до ${_time(paused)}',
      );
    }

    // 5. Ликвидность: наш объём не должен быть заметен в стакане.
    if (dailyTurnover != null && dailyTurnover > 0) {
      final perUnit = spec.riskPerUnit(signal.unitRisk);
      if (perUnit > 0) {
        final units = risk.riskRub / perUnit;
        if (units < 1) {
          return RiskDecision.reject('риск на сделку меньше одной единицы объёма');
        }
        final notional = units * signal.entry.abs() * spec.valuePerPoint;
        final cap = dailyTurnover * limits.maxTurnoverShare;
        if (notional > cap) {
          return RiskDecision.reject(
            'объём ${_money(notional)} ₽ выше ${_r(limits.maxTurnoverShare * 100)}% '
            'дневного оборота',
          );
        }
      }
    }

    return const RiskDecision.allow();
  }

  /// Результат закрытых сегодня сделок, R.
  double dayResultR(SignalLedger ledger, DateTime now) {
    var sum = 0.0;
    for (final trade in ledger.closed) {
      final at = trade.closedAt;
      if (at == null) continue;
      if (at.year == now.year && at.month == now.month && at.day == now.day) {
        sum += trade.resultR ?? 0;
      }
    }
    return sum;
  }

  /// До какого момента действует пауза после серии стопов. null — паузы нет.
  DateTime? pausedUntil(SignalLedger ledger, DateTime now) {
    final done = ledger.closed;
    if (done.length < limits.stopsBeforePause) return null;
    final recent = [...done]..sort((a, b) =>
        (a.closedAt ?? a.createdAt).compareTo(b.closedAt ?? b.createdAt));
    final tail = recent.sublist(recent.length - limits.stopsBeforePause);
    // Стопом считается результат около −1R: частичный тейк с последующим
    // стопом — это не серия убытков, а нормальная работа стратегии.
    if (tail.any((t) => (t.resultR ?? 0) > -0.9)) return null;
    final last = tail.last.closedAt;
    if (last == null) return null;
    final until = last.add(limits.pauseAfterStops);
    return until.isAfter(now) ? until : null;
  }

  /// Снимок состояния для интерфейса.
  RiskState stateOf(SignalLedger ledger, DateTime now) => RiskState(
        open: ledger.openOrPending.length,
        maxConcurrent: limits.maxConcurrent,
        dayResultR: dayResultR(ledger, now),
        dailyLossLimitR: limits.dailyLossLimitR,
        pausedUntil: pausedUntil(ledger, now),
      );

  static String _r(double v) => v.toStringAsFixed(1).replaceAll('.', ',');

  static String _money(double v) => v
      .round()
      .toString()
      .replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ' ');

  static String _time(DateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}
