import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/analysis/backtester.dart';
import 'package:signalai/domain/analysis/factor_audit.dart';
import 'package:signalai/domain/analysis/screener.dart';

/// Аудит отвечает на вопрос «какой фактор реально приносит деньги».
/// Ошибиться здесь опаснее, чем не считать вовсе: подгонка весов под шум —
/// самый дорогой способ испортить рабочую стратегию.
void main() {
  ScoreComponent component(String name, double share) => ScoreComponent(
        name: name,
        points: 10 * share,
        maxPoints: 10,
        note: '',
      );

  BacktestTrade trade(double resultR, List<ScoreComponent> components) =>
      BacktestTrade(symbol: 'TSTU6', resultR: resultR, components: components);

  test('фактор с положительным эджем виден и посчитан', () {
    final trades = [
      for (var i = 0; i < 12; i++) trade(1.0, [component('Структура', 1.0)]),
      for (var i = 0; i < 12; i++) trade(-0.5, [component('Структура', 0.0)]),
    ];

    final edges = computeFactorEdges(trades);
    expect(edges, hasLength(1));
    final structure = edges.single;
    expect(structure.factor, 'Структура');
    expect(structure.withCount, 12);
    expect(structure.withoutCount, 12);
    expect(structure.withAvgR, closeTo(1.0, 1e-9));
    expect(structure.withoutAvgR, closeTo(-0.5, 1e-9));
    expect(structure.edge, closeTo(1.5, 1e-9));
    expect(structure.significant, isTrue);
  });

  test('мёртвый фактор даёт эдж около нуля', () {
    final trades = [
      for (var i = 0; i < 12; i++) trade(0.4, [component('Шум', 1.0)]),
      for (var i = 0; i < 12; i++) trade(0.4, [component('Шум', 0.0)]),
    ];

    expect(computeFactorEdges(trades).single.edge, closeTo(0, 1e-9));
  });

  test('малая выборка помечается как незначимая', () {
    final trades = [
      trade(3.0, [component('Удача', 1.0)]),
      trade(-1.0, [component('Удача', 0.0)]),
    ];

    final edge = computeFactorEdges(trades).single;
    expect(edge.edge, greaterThan(0));
    expect(edge.significant, isFalse, reason: 'две сделки — это не вывод');
  });

  test('порог присутствия фактора — доля от максимума', () {
    final trades = [
      // 0,6 — ровно порог: фактор считается выраженным.
      trade(1.0, [component('Порог', 0.6)]),
      trade(-1.0, [component('Порог', 0.59)]),
    ];

    final edge = computeFactorEdges(trades).single;
    expect(edge.withCount, 1);
    expect(edge.withoutCount, 1);
  });

  test('сильнее влияющие факторы идут первыми', () {
    final trades = [
      for (var i = 0; i < 10; i++)
        trade(2.0, [component('Сильный', 1.0), component('Слабый', 1.0)]),
      for (var i = 0; i < 10; i++)
        trade(-2.0, [component('Сильный', 0.0), component('Слабый', 0.9)]),
    ];

    expect(computeFactorEdges(trades).first.factor, 'Сильный');
  });

  test('сделки без компонент аудит не ломают', () {
    expect(computeFactorEdges([trade(1.0, const [])]), isEmpty);
    expect(computeFactorEdges(const []), isEmpty);
  });

  test('эдж сериализуется и восстанавливается', () {
    final edge = computeFactorEdges([
      trade(1.0, [component('Структура', 1.0)]),
      trade(0.0, [component('Структура', 0.0)]),
    ]).single;

    final restored = FactorEdge.fromJson(edge.toJson());
    expect(restored.factor, edge.factor);
    expect(restored.edge, closeTo(edge.edge, 1e-9));
  });
}
