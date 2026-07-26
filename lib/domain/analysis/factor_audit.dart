import 'backtester.dart';

/// Эдж одного фактора: чем сделки с ним отличаются от сделок без него.
class FactorEdge {
  const FactorEdge({
    required this.factor,
    required this.withCount,
    required this.withoutCount,
    required this.withAvgR,
    required this.withoutAvgR,
  });

  final String factor;
  final int withCount;
  final int withoutCount;

  /// Средний итог сделок, где фактор был выражен, R.
  final double withAvgR;

  /// Средний итог сделок, где фактора не было, R.
  final double withoutAvgR;

  /// Разница средних — сколько R приносит сам фактор.
  double get edge => withAvgR - withoutAvgR;

  /// Хватает ли данных, чтобы разницу вообще обсуждать.
  ///
  /// Порог намеренно грубый: на десятке сделок с каждой стороны разница
  /// средних — это шум, и подавать её как вывод было бы враньём.
  bool get significant => withCount >= 10 && withoutCount >= 10;

  Map<String, dynamic> toJson() => {
        'factor': factor,
        'with_count': withCount,
        'without_count': withoutCount,
        'with_avg_r': withAvgR,
        'without_avg_r': withoutAvgR,
      };

  factory FactorEdge.fromJson(Map<String, dynamic> j) => FactorEdge(
        factor: j['factor'] as String? ?? '',
        withCount: (j['with_count'] as num?)?.toInt() ?? 0,
        withoutCount: (j['without_count'] as num?)?.toInt() ?? 0,
        withAvgR: (j['with_avg_r'] as num?)?.toDouble() ?? 0,
        withoutAvgR: (j['without_avg_r'] as num?)?.toDouble() ?? 0,
      );
}

/// Доля от максимума, начиная с которой фактор считается выраженным.
const factorPresenceShare = 0.6;

/// Аудит факторов по уже сыгранным сделкам прогона.
///
/// Второго движка здесь намеренно нет: сделки приходят из [Backtester] вместе
/// с компонентами оценки, с которыми они были открыты. Поэтому аудит меряет
/// ровно ту стратегию, которая торговалась, — с теми же издержками, стопами и
/// горизонтом, а не абстрактную форвард-доходность «через N часов».
///
/// Итог каждой сделки — её результат в R по горизонту удержания
/// (`maxHoldBars`, по умолчанию 120 часов ≈ 5 дней).
List<FactorEdge> computeFactorEdges(List<BacktestTrade> trades) {
  final withR = <String, List<double>>{};
  final withoutR = <String, List<double>>{};

  for (final trade in trades) {
    for (final component in trade.components) {
      final share = component.maxPoints <= 0 ? 0.0 : component.points / component.maxPoints;
      final bucket = share >= factorPresenceShare ? withR : withoutR;
      (bucket[component.name] ??= []).add(trade.resultR);
    }
  }

  double average(List<double>? values) {
    if (values == null || values.isEmpty) return 0;
    return values.reduce((a, b) => a + b) / values.length;
  }

  final names = {...withR.keys, ...withoutR.keys}.toList()..sort();
  final edges = [
    for (final name in names)
      FactorEdge(
        factor: name,
        withCount: withR[name]?.length ?? 0,
        withoutCount: withoutR[name]?.length ?? 0,
        withAvgR: average(withR[name]),
        withoutAvgR: average(withoutR[name]),
      ),
  ];
  // Сильнее всего влияющие — сверху; знак не важен, мёртвый фактор со
  // стабильно отрицательным эджем не менее интересен, чем работающий.
  edges.sort((a, b) => b.edge.abs().compareTo(a.edge.abs()));
  return edges;
}
