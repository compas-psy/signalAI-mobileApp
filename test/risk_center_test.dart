import 'package:flutter_test/flutter_test.dart';

import 'support/score_fixture.dart';
import 'package:signalai/domain/idea/risk_center.dart';
import 'package:signalai/domain/idea/trade_plan.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';

final now = DateTime.utc(2026, 7, 29, 18);

PaperTrade trade({
  required String id,
  required double? resultR,
  DateTime? closedAt,
  String strategyId = 'forts',
}) =>
    PaperTrade(
      status: closedAt == null ? PaperStatus.open : PaperStatus.closed,
      id: id,
      symbol: id,
      strategyId: strategyId,
      long: true,
      entry: 100,
      stopLoss: 96,
      tpPrices: const [108, 112],
      tpShares: const [50, 50],
      score: 80,
      createdAt: now.subtract(const Duration(days: 2)),
      closedAt: closedAt,
      resultR: resultR,
    );


void main() {
  group('Расход лимитов (ТЗ §20)', () {
    test('пустой журнал оставляет лимиты нетронутыми', () {
      final center = RiskCenter.fromLedger(
        SignalLedger(trades: const [], rejected: const []),
        now: now,
        riskPerTradePercent: 1.0,
      );
      expect(center.daily.remainingPercent, RiskBudget.dailyLossPercent);
      expect(center.openRisk.remainingPercent, RiskBudget.openRiskPercent);
      expect(center.blocked, isFalse);
    });

    test('убыток расходует лимит, прибыль его не восстанавливает', () {
      // Иначе удачное утро открывало бы право проиграть вдвое больше к вечеру.
      final center = RiskCenter.fromLedger(
        SignalLedger(
          trades: [
            trade(id: 'a', resultR: -1, closedAt: now.subtract(const Duration(hours: 2))),
            trade(id: 'b', resultR: 3, closedAt: now.subtract(const Duration(hours: 1))),
          ],
          rejected: const [],
        ),
        now: now,
        riskPerTradePercent: 1.0,
      );
      expect(center.daily.usedPercent, 1.0);
      // Дневной лимит engine-ТЗ — 1,5%, а не 3% из UX-ТЗ.
      expect(center.daily.remainingPercent, 0.5);
    });

    test('старый убыток не расходует дневной лимит, но виден в месячном', () {
      final center = RiskCenter.fromLedger(
        SignalLedger(
          trades: [
            trade(id: 'a', resultR: -2, closedAt: now.subtract(const Duration(days: 10))),
          ],
          rejected: const [],
        ),
        now: now,
        riskPerTradePercent: 1.0,
      );
      expect(center.daily.usedPercent, 0);
      expect(center.weekly.usedPercent, 0);
      expect(center.monthly.usedPercent, 2.0);
    });

    test('исчерпанный лимит блокирует и называет себя', () {
      final center = RiskCenter.fromLedger(
        SignalLedger(
          trades: [
            trade(id: 'a', resultR: -3, closedAt: now.subtract(const Duration(hours: 1))),
          ],
          rejected: const [],
        ),
        now: now,
        riskPerTradePercent: 1.0,
      );
      expect(center.daily.exhausted, isTrue);
      expect(center.blocked, isTrue);
      expect(center.tightest.name, 'День');
      expect(center.daily.fill, 1.0);
    });

    test('открытые позиции занимают открытый риск и кластер', () {
      final center = RiskCenter.fromLedger(
        SignalLedger(
          trades: [
            trade(id: 'a', resultR: null),
            trade(id: 'b', resultR: null),
          ],
          rejected: const [],
        ),
        now: now,
        riskPerTradePercent: 0.75,
      );
      expect(center.openRisk.usedPercent, closeTo(1.5, 1e-9));
      expect(center.openRisk.remainingPercent, closeTo(0.5, 1e-9));
      // Обе по одной стратегии — это одна ставка двойным размером.
      expect(center.clusterRisk.usedPercent, closeTo(1.5, 1e-9));
      expect(center.clusterRisk.exhausted, isTrue);
    });
  });

  group('Бюджет для идеи', () {
    final center = RiskCenter.fromLedger(
      SignalLedger(trades: const [], rejected: const []),
      now: now,
      riskPerTradePercent: 1.0,
    );

    test('оценка задаёт потолок, лимиты — пол', () {
      expect(center.budgetFor(scoreOf(85)).percent, 0.75);
      // Ступени 1% из UX-ТЗ нет: выше 0,75% engine-ТЗ не поднимается.
      expect(center.budgetFor(scoreOf(95)).percent, 0.75);
      expect(center.budgetFor(scoreOf(77)).percent, 0.50);
    });

    test('идея ниже порога входа не получает риска', () {
      expect(center.budgetFor(scoreOf(70)).percent, 0);
      expect(center.budgetFor(scoreOf(70)).blocked, isTrue);
    });

    test('исчерпанный дневной лимит сильнее любой оценки', () {
      final spent = RiskCenter.fromLedger(
        SignalLedger(
          trades: [
            trade(id: 'a', resultR: -3, closedAt: now.subtract(const Duration(hours: 1))),
          ],
          rejected: const [],
        ),
        now: now,
        riskPerTradePercent: 1.0,
      );
      expect(spent.budgetFor(scoreOf(100)).percent, 0);
      expect(spent.budgetFor(scoreOf(100)).binding.name, 'дневной лимит');
    });
  });
}
