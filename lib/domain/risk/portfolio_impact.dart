import '../ledger/money.dart';

/// Вердикт риск-проверки: разрешено, с оговоркой или запрещено.
enum RiskVerdict {
  ok('в пределах'),
  warning('на границе'),
  blocking('превышен');

  const RiskVerdict(this.label);
  final String label;
}

/// Одна проверка перед отправкой заявки.
///
/// Проверка возвращает не «да/нет», а объяснение: человек должен понимать,
/// что именно упирается в лимит, — иначе он снимет лимит, а не сделку.
class RiskCheck {
  const RiskCheck({
    required this.name,
    required this.verdict,
    required this.detail,
  });

  final String name;
  final RiskVerdict verdict;

  /// Числа проверки словами: «открытый риск станет 4,2% из 6%».
  final String detail;
}

/// Что сделка сделает с портфелем.
///
/// Идея, оценённая в вакууме, — это половина решения: одна и та же сделка
/// приемлема на пустом счёте и недопустима, когда три позиции уже держат
/// риск у лимита. Здесь считается именно это.
class PortfolioImpact {
  const PortfolioImpact({
    required this.riskPerTrade,
    required this.openRiskBefore,
    required this.openRiskAfter,
    required this.riskLimit,
    required this.equity,
    required this.checks,
  });

  /// Риск этой сделки в деньгах.
  final Money riskPerTrade;

  /// Открытый риск до и после сделки.
  final Money openRiskBefore;
  final Money openRiskAfter;

  /// Лимит открытого риска в деньгах.
  final Money riskLimit;

  /// Совокупный капитал, от которого считаются доли.
  final Money equity;

  final List<RiskCheck> checks;

  /// Доля открытого риска после сделки в процентах капитала.
  double get openRiskPercent =>
      equity.isZero ? 0 : openRiskAfter.minor / equity.minor * 100;

  /// Есть ли блокирующая проверка.
  bool get blocked => checks.any((c) => c.verdict == RiskVerdict.blocking);

  /// Считает влияние сделки на портфель.
  ///
  /// [openRisk] — сумма расстояний до стопов по уже открытым позициям;
  /// [limitPercent] — предел открытого риска в процентах капитала.
  static PortfolioImpact compute({
    required Money riskPerTrade,
    required Money openRisk,
    required Money equity,
    required double limitPercent,
    required int openPositions,
    required int maxPositions,
    bool correlated = false,
  }) {
    final after = openRisk + riskPerTrade;
    final limit = equity.scaleBy(limitPercent / 100);

    final checks = <RiskCheck>[
      RiskCheck(
        name: 'Открытый риск',
        verdict: after > limit
            ? RiskVerdict.blocking
            : after.minor > limit.minor * 0.8
                ? RiskVerdict.warning
                : RiskVerdict.ok,
        detail: 'станет ${_percent(after, equity)} из '
            '${limitPercent.toStringAsFixed(0)}% капитала',
      ),
      RiskCheck(
        name: 'Одновременных сделок',
        verdict: openPositions >= maxPositions
            ? RiskVerdict.blocking
            : openPositions == maxPositions - 1
                ? RiskVerdict.warning
                : RiskVerdict.ok,
        detail: 'станет ${openPositions + 1} из $maxPositions',
      ),
      if (correlated)
        const RiskCheck(
          name: 'Корреляция',
          verdict: RiskVerdict.warning,
          detail: 'уже есть позиция той же группы — это одна ставка, а не две',
        ),
    ];

    return PortfolioImpact(
      riskPerTrade: riskPerTrade,
      openRiskBefore: openRisk,
      openRiskAfter: after,
      riskLimit: limit,
      equity: equity,
      checks: checks,
    );
  }

  static String _percent(Money value, Money base) {
    if (base.isZero) return '—';
    final pct = value.minor / base.minor * 100;
    return '${pct.toStringAsFixed(1).replaceAll('.', ',')}%';
  }
}
