import '../ledger/signal_ledger.dart';
import 'quality_score.dart';
import 'trade_plan.dart';

/// Использование лимита: сколько истрачено и сколько осталось.
class LimitUsage {
  const LimitUsage({
    required this.name,
    required this.limitPercent,
    required this.usedPercent,
    required this.window,
  });

  final String name;

  /// Потолок из ТЗ §20, % капитала.
  final double limitPercent;

  /// Сколько уже израсходовано, % капитала. Не бывает отрицательным:
  /// прибыль не увеличивает право на риск.
  final double usedPercent;

  /// «сегодня», «на неделе», «в этом месяце», «сейчас».
  final String window;

  double get remainingPercent {
    final left = limitPercent - usedPercent;
    return left < 0 ? 0 : left;
  }

  double get fill =>
      limitPercent <= 0 ? 0 : (usedPercent / limitPercent).clamp(0.0, 1.0);

  bool get exhausted => remainingPercent <= 0;
}

/// Состояние риска по ТЗ §20 — то, что показывает Risk Center и на что
/// опирается финальная проверка.
///
/// Лимиты считаются от журнала в R и переводятся в проценты капитала через
/// риск на сделку: одна сделка риском 0,75% при результате −1R стоит ровно
/// 0,75% капитала. Это не оценка, а прямой пересчёт.
class RiskCenter {
  const RiskCenter({
    required this.daily,
    required this.weekly,
    required this.monthly,
    required this.openRisk,
    required this.clusterRisk,
  });

  final LimitUsage daily;
  final LimitUsage weekly;
  final LimitUsage monthly;
  final LimitUsage openRisk;
  final LimitUsage clusterRisk;

  List<LimitUsage> get all => [daily, weekly, monthly, openRisk, clusterRisk];

  /// Лимит, который ближе всех к исчерпанию.
  LimitUsage get tightest {
    var worst = all.first;
    for (final u in all) {
      if (u.remainingPercent < worst.remainingPercent) worst = u;
    }
    return worst;
  }

  bool get blocked => all.any((u) => u.exhausted);

  /// Бюджет для идеи с данной оценкой (ТЗ §20.1).
  RiskBudget budgetFor(QualityScore score, {double clusterRemaining = -1}) =>
      RiskBudget(
        scoreRiskPercent: score.riskPercent ?? 0,
        remainingDailyPercent: daily.remainingPercent,
        remainingWeeklyPercent: weekly.remainingPercent,
        remainingMonthlyPercent: monthly.remainingPercent,
        remainingOpenRiskPercent: openRisk.remainingPercent,
        remainingClusterPercent:
            clusterRemaining >= 0 ? clusterRemaining : clusterRisk.remainingPercent,
      );

  /// Собрать состояние из журнала.
  ///
  /// [riskPerTradePercent] — риск на сделку в процентах капитала: через него
  /// результат в R превращается в проценты.
  static RiskCenter fromLedger(
    SignalLedger ledger, {
    required DateTime now,
    required double riskPerTradePercent,
  }) {
    final closed = ledger.closed;
    double lossIn(Duration window) {
      final since = now.subtract(window);
      var sum = 0.0;
      for (final trade in closed) {
        final at = trade.closedAt;
        if (at == null || at.isBefore(since)) continue;
        final r = trade.resultR ?? 0;
        // Убыток расходует лимит; прибыль его не восстанавливает — иначе
        // удачное утро открыло бы право проиграть вдвое больше к вечеру.
        if (r < 0) sum += -r * riskPerTradePercent;
      }
      return sum;
    }

    final open = ledger.openOrPending;
    final openRiskUsed = open.length * riskPerTradePercent;

    // Кластер: самая крупная группа коррелированных открытых позиций.
    final byGroup = <String, int>{};
    for (final trade in open) {
      byGroup.update(trade.strategyId, (v) => v + 1, ifAbsent: () => 1);
    }
    final largestCluster =
        byGroup.values.fold(0, (a, b) => a > b ? a : b) * riskPerTradePercent;

    return RiskCenter(
      daily: LimitUsage(
        name: 'День',
        limitPercent: RiskBudget.dailyLossPercent,
        usedPercent: lossIn(const Duration(days: 1)),
        window: 'сегодня',
      ),
      weekly: LimitUsage(
        name: 'Неделя',
        limitPercent: RiskBudget.weeklyLossPercent,
        usedPercent: lossIn(const Duration(days: 7)),
        window: 'за 7 дней',
      ),
      monthly: LimitUsage(
        name: 'Месяц',
        limitPercent: RiskBudget.monthlyLossPercent,
        usedPercent: lossIn(const Duration(days: 30)),
        window: 'за 30 дней',
      ),
      openRisk: LimitUsage(
        name: 'Открытый риск',
        limitPercent: RiskBudget.openRiskPercent,
        usedPercent: openRiskUsed,
        window: 'сейчас',
      ),
      clusterRisk: LimitUsage(
        name: 'Кластер',
        limitPercent: RiskBudget.clusterRiskPercent,
        usedPercent: largestCluster,
        window: 'сейчас',
      ),
    );
  }
}
