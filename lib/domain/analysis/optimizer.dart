import 'dart:math' as math;

import 'backtester.dart';
import 'screener.dart';

/// Набор настраиваемых параметров стратегии.
class StrategyParams {
  const StrategyParams({
    required this.minScore,
    required this.maxEntryDistanceAtr,
    required this.takeProfitMultiples,
    this.requireTrigger = false,
    this.entryType = EntryType.limit,
    this.align4h = false,
  });

  final int minScore;
  final double maxEntryDistanceAtr;
  final List<double> takeProfitMultiples;

  /// Требовать триггерную свечу у уровня — фильтр качества ретеста.
  final bool requireTrigger;

  /// Способ входа: лимитка на ретесте или стоп-заявка за пробоем.
  final EntryType entryType;

  /// Согласование направления со структурой 4H.
  final bool align4h;

  Screener buildScreener({String timeframeLabel = '1H'}) => Screener(
        minScore: minScore,
        maxEntryDistanceAtr: maxEntryDistanceAtr,
        takeProfitMultiples: takeProfitMultiples,
        requireTrigger: requireTrigger,
        entryType: entryType,
        align4h: align4h,
        timeframeLabel: timeframeLabel,
      );

  Map<String, dynamic> toJson() => {
        'min_score': minScore,
        'max_entry_distance_atr': maxEntryDistanceAtr,
        'tp_multiples': takeProfitMultiples,
        'require_trigger': requireTrigger,
        'entry_type': entryType.id,
        'align_4h': align4h,
      };

  factory StrategyParams.fromJson(Map<String, dynamic> j) => StrategyParams(
        minScore: (j['min_score'] as num).toInt(),
        maxEntryDistanceAtr: (j['max_entry_distance_atr'] as num).toDouble(),
        takeProfitMultiples: [
          for (final m in j['tp_multiples'] as List<dynamic>) (m as num).toDouble(),
        ],
        requireTrigger: j['require_trigger'] as bool? ?? false,
        entryType: EntryType.parse(j['entry_type'] as String?),
        align4h: j['align_4h'] as bool? ?? false,
      );

  static const defaults = StrategyParams(
    minScore: 60,
    maxEntryDistanceAtr: 0.5,
    takeProfitMultiples: Screener.defaultTakeProfitMultiples,
  );

  String get label => 'score ≥ $minScore · вход ≤ '
      '${maxEntryDistanceAtr.toStringAsFixed(2).replaceAll('.', ',')}·ATR · TP '
      '${takeProfitMultiples.map((m) => m.toStringAsFixed(1).replaceAll('.', ',')).join('/')}R'
      '${requireTrigger ? ' · триггер' : ''}'
      '${entryType == EntryType.stopBreak ? ' · вход по пробою' : ''}'
      '${align4h ? ' · 4H согласована' : ''}';
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
    this.minTestTrades = 30,
    this.backtester = const Backtester(),
  });

  final int minTrainTrades;

  /// Сколько сделок out-of-sample нужно, чтобы результат вообще что-то значил.
  ///
  /// Было 4. При таком объёме стандартная ошибка средней R около 0,55, а
  /// победитель отбора получает от чистого шума примерно +0,57 R на сделку —
  /// при том что правдоподобный реальный эдж свинговой системы после издержек
  /// порядка 0,03–0,15 R. То есть решение принималось по шуму, превышающему
  /// сигнал на порядок.
  ///
  /// 30 — компромисс: SE падает до ~0,20 R. Строго говоря, надёжным отбор
  /// становится ближе к 150 сделкам, и на нашей истории столько не наберётся.
  /// Поэтому основную работу делает правило одной стандартной ошибки: на малой
  /// выборке SE велика, в неё попадают почти все кандидаты, и выбирается самый
  /// простой — то есть дефолт. Оптимизатор сам становится консервативным там,
  /// где данных мало, вместо того чтобы гоняться за шумом.
  final int minTestTrades;

  final Backtester backtester;

  /// Сетка кандидатов. Дефолт всегда участвует — он бенчмарк.
  static const List<StrategyParams> grid = [
    StrategyParams.defaults,
    // Числовые вариации порогов — узкая окрестность дефолта.
    StrategyParams(minScore: 55, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.4, 2.2, 3.5]),
    StrategyParams(minScore: 65, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.4, 2.2, 3.5]),
    StrategyParams(minScore: 60, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.2, 2.0, 3.0]),
    // Фильтр качества ретеста: вход только при реакции цены на уровень.
    StrategyParams(
        minScore: 60, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.4, 2.2, 3.5], requireTrigger: true),
    // Структурные варианты. Числовые ручки дефолт не обыграли — значит, эдж
    // ищется не в порогах, а в самом способе входа и в старшем контексте.
    StrategyParams(
        minScore: 60,
        maxEntryDistanceAtr: 0.5,
        takeProfitMultiples: [1.4, 2.2, 3.5],
        entryType: EntryType.stopBreak),
    StrategyParams(
        minScore: 60,
        maxEntryDistanceAtr: 0.5,
        takeProfitMultiples: [1.4, 2.2, 3.5],
        entryType: EntryType.stopBreak,
        requireTrigger: true),
    StrategyParams(
        minScore: 60, maxEntryDistanceAtr: 0.5, takeProfitMultiples: [1.4, 2.2, 3.5], align4h: true),
    StrategyParams(
        minScore: 60,
        maxEntryDistanceAtr: 0.5,
        takeProfitMultiples: [1.4, 2.2, 3.5],
        align4h: true,
        entryType: EntryType.stopBreak),
    StrategyParams(
        minScore: 60,
        maxEntryDistanceAtr: 0.5,
        takeProfitMultiples: [1.4, 2.2, 3.5],
        align4h: true,
        requireTrigger: true),
    StrategyParams(
        minScore: 60,
        maxEntryDistanceAtr: 0.5,
        takeProfitMultiples: [1.2, 2.0, 3.0],
        entryType: EntryType.stopBreak),
    StrategyParams(
        minScore: 55,
        maxEntryDistanceAtr: 0.5,
        takeProfitMultiples: [1.4, 2.2, 3.5],
        align4h: true,
        entryType: EntryType.stopBreak,
        requireTrigger: true),
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

    // 2. ВСЯ сетка проверяется на невиданных данных, а не топ-3.
    //
    // Раньше на OOS уходили только три лучших по train, и победитель среди них
    // выбирался по максимуму. Это две отдельные ошибки. Первая: выбор
    // максимума из k результатов даёт смещение — победитель получает от шума
    // примерно SE·c(k), и при четырёх сделках это около +0,57 R на сделку,
    // тогда как правдоподобный реальный эдж свинговой системы после издержек
    // порядка 0,03–0,15 R. Шум превышал сигнал на порядок. Вторая: отбор шёл
    // по трём испытаниям, а фактически их было двенадцать.
    final defaultTest = await _runTail(build(StrategyParams.defaults), test, train, regime);
    final tested = <(StrategyParams, BacktestSummary)>[];
    for (final (params, _) in candidates) {
      onProgress?.call('Проверка на новых данных: ${params.label}');
      final summary = await _runTail(build(params), test, train, regime);
      if (summary.count >= minTestTrades) tested.add((params, summary));
    }

    // 3. Правило одной стандартной ошибки.
    //
    // Из кандидатов, попавших в одну SE от лучшего, берём самый простой —
    // ближайший к дефолту. Разница внутри одной SE статистически неотличима
    // от шума, и выбирать по ней значит подгонять. Дефолт остаётся бенчмарком:
    // если он не обыгран уверенно, менять конфигурацию не за что.
    var best = StrategyParams.defaults;
    var bestSummary = defaultTest;
    if (tested.isNotEmpty) {
      final leader = tested.reduce((a, b) => _score(a.$2) >= _score(b.$2) ? a : b);
      final threshold = _score(leader.$2) - _standardError(leader.$2);
      final withinOneSe = [
        for (final entry in tested)
          if (_score(entry.$2) >= threshold) entry,
      ];
      // Простейший — тот, у кого меньше отличий от дефолта.
      withinOneSe.sort((a, b) =>
          _distanceFromDefault(a.$1).compareTo(_distanceFromDefault(b.$1)));
      final pick = withinOneSe.first;
      if (_score(pick.$2) > _score(defaultTest)) {
        best = pick.$1;
        bestSummary = pick.$2;
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

  /// Стандартная ошибка средней R на выборке прогона.
  ///
  /// Разброс результата одной сделки при нашей механике (стоп −1R, безубыток
  /// после первого тейка, полный набор целей около +2R) даёт σ порядка 1,1 R.
  /// Точнее оценивать не из чего: распределение по сделкам прогон не хранит.
  static double _standardError(BacktestSummary summary) =>
      summary.count <= 1 ? double.infinity : 1.1 / math.sqrt(summary.count);

  /// Насколько вариант отличается от дефолта. Меньше — проще.
  static int _distanceFromDefault(StrategyParams params) {
    final d = StrategyParams.defaults;
    var distance = 0;
    if (params.minScore != d.minScore) distance++;
    if (params.requireTrigger != d.requireTrigger) distance++;
    if (params.entryType != d.entryType) distance++;
    if (params.align4h != d.align4h) distance++;
    if (params.maxEntryDistanceAtr != d.maxEntryDistanceAtr) distance++;
    if (params.takeProfitMultiples.join(',') != d.takeProfitMultiples.join(',')) {
      distance++;
    }
    return distance;
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
