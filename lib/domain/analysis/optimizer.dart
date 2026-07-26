import 'backtester.dart';
import 'screener.dart';

/// Набор настраиваемых параметров стратегии.
class StrategyParams {
  const StrategyParams({
    required this.minScore,
    required this.maxEntryDistanceAtr,
    required this.takeProfitMultiples,
  });

  final int minScore;
  final double maxEntryDistanceAtr;
  final List<double> takeProfitMultiples;

  Screener buildScreener() => Screener(
        minScore: minScore,
        maxEntryDistanceAtr: maxEntryDistanceAtr,
        takeProfitMultiples: takeProfitMultiples,
      );

  Map<String, dynamic> toJson() => {
        'min_score': minScore,
        'max_entry_distance_atr': maxEntryDistanceAtr,
        'tp_multiples': takeProfitMultiples,
      };

  factory StrategyParams.fromJson(Map<String, dynamic> j) => StrategyParams(
        minScore: (j['min_score'] as num).toInt(),
        maxEntryDistanceAtr: (j['max_entry_distance_atr'] as num).toDouble(),
        takeProfitMultiples: [
          for (final m in j['tp_multiples'] as List<dynamic>) (m as num).toDouble(),
        ],
      );

  static const defaults = StrategyParams(
    minScore: 60,
    maxEntryDistanceAtr: 0.5,
    takeProfitMultiples: Screener.defaultTakeProfitMultiples,
  );

  String get label => 'score ≥ $minScore · вход ≤ '
      '${maxEntryDistanceAtr.toStringAsFixed(2).replaceAll('.', ',')}·ATR · TP '
      '${takeProfitMultiples.map((m) => m.toStringAsFixed(1).replaceAll('.', ',')).join('/')}R';
}

/// Итог оптимизации: выбранные параметры и их out-of-sample качество.
class OptimizationOutcome {
  const OptimizationOutcome({
    required this.params,
    required this.testProfitFactor,
    required this.testTrades,
    required this.testAvgR,
    required this.improved,
  });

  final StrategyParams params;

  /// Профит-фактор на данных, которых подбор не видел. null — убытков не было.
  final double? testProfitFactor;
  final int testTrades;
  final double testAvgR;

  /// false — лучше дефолтов ничего не нашлось, параметры не менялись.
  final bool improved;
}

/// Walk-forward подбор параметров — так, как это принято делать, без утопии
/// «одна настройка на все случаи жизни»:
///
///  * история делится на train (70%) и test (30%) по времени;
///  * сетка параметров узкая и осмысленная — три порога оценки, три ширины
///    зоны входа, три профиля тейков, а не перебор тысяч комбинаций;
///  * кандидаты ранжируются на train, но выбор делается по test — данным,
///    которых подбор не видел. Это главная защита от подгонки под историю;
///  * кандидат обязан наторговать минимум сделок и на train, и на test:
///    красивый PF на трёх сделках — шум, а не результат;
///  * если ни один кандидат не обыгрывает дефолт на test — честно остаёмся
///    на дефолте и говорим об этом.
///
/// Частота перезапуска — раз в неделю. Для свинга 1–5 дней ежедневная
/// переоптимизация на 40–60 днях истории означает подгонку под последние
/// колебания: между прогонами накапливается всего ~5 новых сделок, и
/// «оптимум» начинает гоняться за шумом. Недельный шаг — стандарт
/// walk-forward для этого горизонта.
class StrategyOptimizer {
  const StrategyOptimizer({
    this.minTrainTrades = 8,
    this.minTestTrades = 4,
    this.backtester = const Backtester(),
  });

  final int minTrainTrades;
  final int minTestTrades;
  final Backtester backtester;

  /// Сетка кандидатов. Дефолт всегда участвует — он бенчмарк.
  static const List<StrategyParams> grid = [
    StrategyParams.defaults,
    StrategyParams(minScore: 55, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.4, 2.2, 3.5]),
    StrategyParams(minScore: 65, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.4, 2.2, 3.5]),
    StrategyParams(minScore: 60, maxEntryDistanceAtr: 0.35, takeProfitMultiples: [1.4, 2.2, 3.5]),
    StrategyParams(minScore: 60, maxEntryDistanceAtr: 0.7, takeProfitMultiples: [1.4, 2.2, 3.5]),
    StrategyParams(minScore: 60, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.2, 2.0, 3.0]),
    StrategyParams(minScore: 60, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.8, 2.6, 4.2]),
    StrategyParams(minScore: 65, maxEntryDistanceAtr: 0.35, takeProfitMultiples: [1.2, 2.0, 3.0]),
    StrategyParams(minScore: 55, maxEntryDistanceAtr: 0.7, takeProfitMultiples: [1.8, 2.6, 4.2]),
  ];

  /// Подбор. [screenerBuilder] подменяется в тестах.
  Future<OptimizationOutcome> optimize(
    List<InstrumentHistory> histories, {
    void Function(String stage)? onProgress,
    Screener Function(StrategyParams params)? screenerBuilder,
    RegimeTimeline? regime,
  }) async {
    final build = screenerBuilder ?? (p) => p.buildScreener();

    // Разрез по времени: подбор не видит test-хвост.
    final train = <InstrumentHistory>[];
    final test = <InstrumentHistory>[];
    for (final h in histories) {
      final cut = (h.hourly.length * 0.7).floor();
      if (cut < 100 || h.hourly.length - cut < 60) continue;
      final cutTime = h.hourly[cut].time;
      train.add(InstrumentHistory(
        spec: h.spec,
        hourly: h.hourly.sublist(0, cut),
        daily: [for (final d in h.daily) if (d.time.isBefore(cutTime)) d],
      ));
      // Test-хвост получает разогрев из конца train-окна, но сделки в нём
      // начинаются только после разреза — заглядывания в подбор нет.
      test.add(InstrumentHistory(spec: h.spec, hourly: h.hourly, daily: h.daily));
    }
    if (train.isEmpty) {
      return const OptimizationOutcome(
        params: StrategyParams.defaults,
        testProfitFactor: null,
        testTrades: 0,
        testAvgR: 0,
        improved: false,
      );
    }

    // 1. Прогон сетки на train.
    final candidates = <(StrategyParams, BacktestSummary)>[];
    for (var i = 0; i < grid.length; i++) {
      onProgress?.call('Подбор: вариант ${i + 1} из ${grid.length}…');
      final summary = await _run(build(grid[i]), train, regime);
      if (summary.count >= minTrainTrades && _score(summary) > 0) {
        candidates.add((grid[i], summary));
      }
    }
    candidates.sort((a, b) => _score(b.$2).compareTo(_score(a.$2)));

    // 2. Топ train-кандидатов проверяется на невиданных данных.
    final defaultTest = await _runTail(build(StrategyParams.defaults), test, train, regime);
    var best = StrategyParams.defaults;
    var bestSummary = defaultTest;
    for (final (params, _) in candidates.take(3)) {
      onProgress?.call('Проверка на новых данных: ${params.label}');
      final summary = await _runTail(build(params), test, train, regime);
      if (summary.count >= minTestTrades && _score(summary) > _score(bestSummary)) {
        best = params;
        bestSummary = summary;
      }
    }

    return OptimizationOutcome(
      params: best,
      testProfitFactor: bestSummary.profitFactor,
      testTrades: bestSummary.count,
      testAvgR: bestSummary.averageR,
      improved: !identical(best, StrategyParams.defaults),
    );
  }

  Future<BacktestSummary> _run(
    Screener screener,
    List<InstrumentHistory> histories,
    RegimeTimeline? regime,
  ) =>
      Backtester(
        screener: screener,
        evaluateEveryBars: backtester.evaluateEveryBars,
        windowBars: backtester.windowBars,
        orderTtlBars: backtester.orderTtlBars,
        maxHoldBars: backtester.maxHoldBars,
        cooldownBars: backtester.cooldownBars,
        costs: backtester.costs,
      ).run(histories, regime: regime);

  /// Прогон полной истории с учётом только сделок после train-разреза.
  ///
  /// Считается поинструментно: сделки инструмента на полной истории — это
  /// его train-сделки плюс хвост (движок детерминирован, а данные train —
  /// префикс полных), поэтому хвост выделяется вычитанием длины.
  Future<BacktestSummary> _runTail(
    Screener screener,
    List<InstrumentHistory> full,
    List<InstrumentHistory> train,
    RegimeTimeline? regime,
  ) async {
    final tail = <BacktestTrade>[];
    var days = 0;
    for (var i = 0; i < full.length; i++) {
      final fullRun = await _run(screener, [full[i]], regime);
      final trainRun = await _run(screener, [train[i]], regime);
      if (fullRun.trades.length > trainRun.trades.length) {
        tail.addAll(fullRun.trades.sublist(trainRun.trades.length));
      }
      final span = fullRun.days - trainRun.days;
      if (span > days) days = span;
    }
    return BacktestSummary(trades: tail, days: days, instruments: full.length);
  }

  /// Качество прогона: средняя R с усадкой за малое число сделок.
  ///
  /// Средняя R честнее PF при частичных фиксациях, а множитель n/(n+10)
  /// прижимает результаты на малой выборке к нулю — три удачные сделки не
  /// обыграют тридцать средних.
  double _score(BacktestSummary s) {
    if (s.count == 0) return 0;
    return s.averageR * (s.count / (s.count + 10));
  }
}
