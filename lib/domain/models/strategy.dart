import '../enums.dart';
import 'portfolio.dart';

/// Пакет стратегий (ТЗ §5.5: стратегия = версионируемый конфиг).
class StrategyPack {
  const StrategyPack({
    required this.id,
    required this.name,
    required this.description,
    required this.statsLabel,
    required this.enabled,
    this.mode = StrategyMode.paper,
    this.horizon = Horizon.swing,
    this.version = 1,
    this.closedPaperTrades = 0,
  });

  final String id;
  final String name;
  final String description;

  /// Готовая строка статистики: «WR 61% · PF 2,1 · 38 сделок / 90д».
  final String statsLabel;
  final bool enabled;
  final StrategyMode mode;
  final Horizon horizon;

  /// Версия конфига — журнал привязан к ней (ТЗ §5.5).
  final int version;

  /// Закрытых бумажных сделок: перевод в live доступен от 20 (ТЗ §1).
  final int closedPaperTrades;

  bool get canGoLive => closedPaperTrades >= 20;

  StrategyPack copyWith({bool? enabled, StrategyMode? mode}) => StrategyPack(
        id: id,
        name: name,
        description: description,
        statsLabel: statsLabel,
        enabled: enabled ?? this.enabled,
        mode: mode ?? this.mode,
        horizon: horizon,
        version: version,
        closedPaperTrades: closedPaperTrades,
      );

  factory StrategyPack.fromJson(Map<String, dynamic> j) => StrategyPack(
        id: j['id'] as String,
        name: j['name'] as String,
        description: j['description'] as String? ?? '',
        statsLabel: j['stats_label'] as String? ?? '',
        enabled: j['enabled'] as bool? ?? false,
        mode: StrategyMode.parse(j['mode'] as String? ?? 'paper'),
        horizon: Horizon.parse(j['horizon'] as String? ?? 'swing'),
        version: (j['version'] as num?)?.toInt() ?? 1,
        closedPaperTrades: (j['closed_paper_trades'] as num?)?.toInt() ?? 0,
      );
}

/// Строка «параметр → значение» в карточке параметров стратегии.
class StrategyParam {
  const StrategyParam({required this.name, required this.value});

  final String name;
  final String value;

  factory StrategyParam.fromJson(Map<String, dynamic> j) =>
      StrategyParam(name: j['name'] as String, value: j['value'] as String);
}

/// Результат прогона бэктеста (ТЗ §5.5, paper-режим).
class BacktestResult {
  const BacktestResult({
    required this.info,
    required this.stats,
    required this.equityCurve,
  });

  /// «180 дней · 221 сделка · только что».
  final String info;
  final List<StatTile> stats;
  final List<double> equityCurve;

  factory BacktestResult.fromJson(Map<String, dynamic> j) => BacktestResult(
        info: j['info'] as String? ?? '',
        stats: (j['stats'] as List<dynamic>? ?? const [])
            .map((e) => StatTile.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        equityCurve: (j['equity_curve'] as List<dynamic>? ?? const [])
            .map((e) => (e as num).toDouble())
            .toList(growable: false),
      );

  Map<String, dynamic> toJson() => {
        'info': info,
        'stats': [for (final s in stats) s.toJson()],
        'equity_curve': equityCurve,
      };
}

/// Строка таблицы эджа факторов: «фактор → сколько R он приносит».
class FactorEdgeRow {
  const FactorEdgeRow({
    required this.factor,
    required this.edgeR,
    required this.withCount,
    required this.withoutCount,
    required this.significant,
  });

  final String factor;

  /// Разница средних результатов «фактор есть» и «фактора нет», R.
  final double edgeR;
  final int withCount;
  final int withoutCount;

  /// Хватает ли выборки, чтобы разницу вообще обсуждать.
  final bool significant;
}

/// Живое состояние риск-лимитов для карточки на экране «Стратегии».
class RiskLimitsView {
  const RiskLimitsView({
    required this.open,
    required this.maxConcurrent,
    required this.dayResultR,
    required this.dailyLossLimitR,
    required this.pauseNote,
  });

  final int open;
  final int maxConcurrent;
  final double dayResultR;
  final double dailyLossLimitR;

  /// «пауза не активна» либо «пауза до 14:20».
  final String pauseNote;
}

/// Данные экрана «Стратегии».
class StrategiesSnapshot {
  const StrategiesSnapshot({
    required this.packs,
    required this.paramsTitle,
    required this.params,
    required this.backtest,
    this.factorEdges = const [],
    this.riskLimits,
  });

  final List<StrategyPack> packs;

  /// «Параметры · Интеграционная».
  final String paramsTitle;
  final List<StrategyParam> params;
  final BacktestResult backtest;

  /// Эдж факторов последнего прогона. Пусто — прогона ещё не было.
  final List<FactorEdgeRow> factorEdges;

  /// Состояние риск-лимитов. null — режим без риск-движка (демо).
  final RiskLimitsView? riskLimits;

  StrategiesSnapshot copyWith({
    List<StrategyPack>? packs,
    BacktestResult? backtest,
    List<FactorEdgeRow>? factorEdges,
  }) =>
      StrategiesSnapshot(
        packs: packs ?? this.packs,
        paramsTitle: paramsTitle,
        params: params,
        backtest: backtest ?? this.backtest,
        factorEdges: factorEdges ?? this.factorEdges,
        riskLimits: riskLimits,
      );
}
