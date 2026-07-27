import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/ledger/money.dart';
import 'package:signalai/domain/risk/portfolio_impact.dart';

Money rub(num v) => Money.of(v, Currency.rub);

PortfolioImpact impact({
  num perTrade = 18000,
  num openRisk = 0,
  num equity = 2400000,
  double limitPercent = 2.25,
  int open = 0,
  int max = 3,
  bool correlated = false,
}) =>
    PortfolioImpact.compute(
      riskPerTrade: rub(perTrade),
      openRisk: rub(openRisk),
      equity: rub(equity),
      limitPercent: limitPercent,
      openPositions: open,
      maxPositions: max,
      correlated: correlated,
    );

void main() {
  group('Влияние сделки на портфель', () {
    test('на пустом счёте сделка проходит', () {
      final result = impact();
      expect(result.blocked, isFalse);
      expect(result.openRiskAfter, rub(18000));
      expect(result.checks.first.verdict, RiskVerdict.ok);
    });

    test('превышение открытого риска блокирует', () {
      // Лимит 2,25% от 2,4 млн = 54 000 ₽. Уже открыто 54 000 — новая сделка
      // выходит за предел.
      final result = impact(openRisk: 54000, open: 2);
      expect(result.blocked, isTrue);
      expect(result.checks.first.verdict, RiskVerdict.blocking);
      expect(result.checks.first.detail, contains('из 2%'));
    });

    test('подход к лимиту предупреждает, а не молчит', () {
      // 80% лимита — уже граница: человек должен видеть её до отправки.
      final result = impact(openRisk: 36000, open: 2);
      expect(result.checks.first.verdict, RiskVerdict.warning);
      expect(result.blocked, isFalse);
    });

    test('лимит одновременных сделок считается по факту', () {
      final result = impact(open: 3, max: 3);
      expect(
        result.checks.firstWhere((c) => c.name == 'Одновременных сделок').verdict,
        RiskVerdict.blocking,
      );
    });

    test('коррелированная позиция — предупреждение с объяснением', () {
      final result = impact(correlated: true);
      final check = result.checks.firstWhere((c) => c.name == 'Корреляция');
      expect(check.verdict, RiskVerdict.warning);
      expect(check.detail, contains('одна ставка'));
    });

    test('доля открытого риска считается от капитала', () {
      final result = impact(perTrade: 24000, openRisk: 0, equity: 2400000);
      expect(result.openRiskPercent, closeTo(1.0, 0.001));
    });

    test('нулевой капитал не роняет расчёт', () {
      final result = impact(equity: 0);
      expect(result.openRiskPercent, 0);
      expect(result.checks.first.detail, contains('—'));
    });
  });
}
