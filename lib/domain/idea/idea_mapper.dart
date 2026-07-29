import '../enums.dart';
import '../models/signal.dart';
import 'evidence.dart';
import 'idea.dart';
import 'idea_state.dart';
import 'quality_score.dart';
import 'trade_plan.dart';

/// Перевод сигнала расчётного контура в идею по ТЗ v2.
///
/// Контур получения данных от бирж и сам скринер остаются прежними — это
/// единственное, что работает и проверено. Меняется то, во что превращается
/// его результат: вместо карточки с текстом — идея с состоянием, планом,
/// доказательствами и разметкой, на которые ссылаются экраны.
///
/// Ничего не выдумывается: чего скринер не измерил, то помечается флагом
/// качества данных и опускает оценку, а не подставляет правдоподобное число.
abstract final class IdeaMapper {
  /// Соответствие блоков скринера факторам ТЗ §14.2.
  ///
  /// Совпадение не полное, и это видно: Price Action и RSI вместе отвечают за
  /// триггер входа, а R/R, ликвидность и событийный риск скринер вообще не
  /// считает — они вычисляются здесь по плану и рыночным данным.
  static const factorOf = <String, ScoreFactor>{
    'Режим рынка': ScoreFactor.marketRegime,
    'Структура · SMC': ScoreFactor.multiTfStructure,
    'Wyckoff': ScoreFactor.multiTfStructure,
    'Smart Money': ScoreFactor.multiTfStructure,
    'Зона входа': ScoreFactor.zoneQuality,
    'Price Action': ScoreFactor.entryTrigger,
    'RSI(14)': ScoreFactor.entryTrigger,
    'Объём / OI': ScoreFactor.volumeOi,
  };

  /// Блоки, сознательно не участвующие в оценке.
  ///
  /// ТЗ §9.1: Эллиотт допустим только как вторичный контекст и не должен
  /// решать вход. Он показывается в разборе, но баллов не даёт — и это
  /// решение записано здесь, а не спрятано в отсутствии строки в таблице.
  static const secondaryOnly = <String>{'Эллиотт'};

  static const detectorVersion = 'screener-1.0.0';

  /// Собрать идею из сигнала.
  ///
  /// [budget] задаёт бюджет риска, [equity] — капитал торгового счёта в
  /// рублях. [turnover] — среднедневной оборот инструмента; null означает
  /// «не измерено», и это честно отражается в оценке ликвидности.
  static Idea fromSignal(
    TradingSignal signal, {
    required RiskBudget budget,
    required double equity,
    required DateTime now,
    double? turnover,
    EventRisk? event,
  }) {
    final evidence = _evidence(signal);
    final annotations = _annotations(signal, evidence);
    final validUntil = signal.validUntil ?? now.add(const Duration(hours: 8));

    final plan = _plan(
      signal,
      budget: budget,
      equity: equity,
      expiry: validUntil,
    );

    final score = _score(
      signal,
      plan: plan,
      turnover: turnover,
      event: event,
      evidence: evidence,
    );

    return Idea(
      id: signal.id,
      instrumentId: signal.symbol,
      instrumentName: signal.name,
      market: signal.market,
      direction: signal.direction,
      strategy: _strategy(signal),
      strategyVersion: signal.strategyId ?? detectorVersion,
      state: _state(signal, now: now, validUntil: validUntil),
      score: score,
      createdAt: validUntil.subtract(const Duration(hours: 8)),
      validUntil: validUntil,
      thesis: signal.note,
      plan: plan,
      timeframes: const ['1D', '4H', '1H'],
      evidence: evidence,
      annotations: annotations,
      eventRisk: event,
      dataFlags: _dataFlags(signal, turnover: turnover),
    );
  }

  /// Состояние идеи по её статусу и сроку (ТЗ §8).
  static IdeaState _state(
    TradingSignal signal, {
    required DateTime now,
    required DateTime validUntil,
  }) {
    if (!now.isBefore(validUntil)) return IdeaState.expired;
    return switch (signal.status) {
      SignalStatus.confirmed ||
      SignalStatus.working ||
      SignalStatus.open =>
        IdeaState.active,
      SignalStatus.closed => IdeaState.closed,
      SignalStatus.invalidated => IdeaState.invalidated,
      SignalStatus.expired => IdeaState.expired,
      SignalStatus.rejected => IdeaState.skipped,
      // Порог пуша скринера (75) совпадает с порогом входа ТЗ §14.2: до него
      // идея наблюдается, после — предъявляется к решению.
      SignalStatus.pushed => IdeaState.triggered,
      SignalStatus.proposed =>
        signal.score >= QualityScore.minimumToShow ? IdeaState.ready : IdeaState.watch,
    };
  }

  static SetupStrategy _strategy(TradingSignal signal) {
    if (signal.entryIsStop) return SetupStrategy.breakoutRetest;
    final chips = signal.chips.join(' ').toLowerCase();
    if (chips.contains('choch')) return SetupStrategy.wyckoffReversal;
    return SetupStrategy.trendPullback;
  }

  /// Доказательства: по одному на блок оценки скринера.
  static List<Evidence> _evidence(TradingSignal signal) => [
        for (var i = 0; i < signal.factors.length; i++)
          Evidence(
            id: '${signal.id}_f$i',
            kind: _kindOf(signal.factors[i].name),
            summary: signal.factors[i].name,
            detail: signal.factors[i].text,
            confidence: signal.factors[i].fraction,
            detectorVersion: detectorVersion,
            annotationIds: _annotationIdsFor(signal, signal.factors[i].name),
          ),
      ];

  static String _kindOf(String factorName) => switch (factorName) {
        'Режим рынка' => 'market_regime',
        'Структура · SMC' => 'smc_context',
        'Зона входа' => 'structure',
        'Price Action' => 'entry_trigger',
        'RSI(14)' => 'entry_trigger',
        'Объём / OI' => 'volume_confirmation',
        _ => 'other',
      };

  static List<String> _annotationIdsFor(TradingSignal signal, String factorName) =>
      switch (factorName) {
        'Структура · SMC' => [
            if (signal.chart?.breakLevel != null) '${signal.id}_break',
            for (var i = 0; i < (signal.chart?.zones.length ?? 0); i++)
              '${signal.id}_zone$i',
          ],
        'Зона входа' => ['${signal.id}_entry'],
        _ => const [],
      };

  /// Разметка графика из того, что скринер действительно нашёл.
  ///
  /// Уровни входа, стопа и целей рисуются всегда — они часть плана. Зоны и
  /// слом структуры появляются только при наличии соответствующего
  /// доказательства (ТЗ §9.1).
  static List<ChartAnnotation> _annotations(
    TradingSignal signal,
    List<Evidence> evidence,
  ) {
    final chart = signal.chart;
    final zoneEvidence =
        evidence.where((e) => e.kind == 'smc_context').firstOrNull;
    final levelEvidence =
        evidence.where((e) => e.kind == 'structure').firstOrNull;
    if (levelEvidence == null) return const [];

    final from = DateTime.fromMillisecondsSinceEpoch(0);
    final to = signal.validUntil ?? from;
    final out = <ChartAnnotation>[
      ChartAnnotation(
        id: '${signal.id}_entry',
        type: AnnotationType.levelEntry,
        timeframe: chart?.timeframeLabel ?? '1H',
        startTime: from,
        endTime: to,
        priceLow: signal.entry,
        priceHigh: signal.entry,
        confidence: 1,
        evidenceId: levelEvidence.id,
        detectorVersion: detectorVersion,
        label: 'Вход',
        displayPriority: 95,
      ),
      ChartAnnotation(
        id: '${signal.id}_stop',
        type: AnnotationType.levelStop,
        timeframe: chart?.timeframeLabel ?? '1H',
        startTime: from,
        endTime: to,
        priceLow: signal.stopLoss,
        priceHigh: signal.stopLoss,
        confidence: 1,
        evidenceId: levelEvidence.id,
        detectorVersion: detectorVersion,
        label: 'Стоп',
        displayPriority: 90,
      ),
      for (final tp in signal.takeProfits)
        ChartAnnotation(
          id: '${signal.id}_tp${tp.index}',
          type: AnnotationType.levelTarget,
          timeframe: chart?.timeframeLabel ?? '1H',
          startTime: from,
          endTime: to,
          priceLow: tp.price,
          priceHigh: tp.price,
          confidence: 1,
          evidenceId: levelEvidence.id,
          detectorVersion: detectorVersion,
          label: '${tp.label} · ${tp.sharePercent}%',
          displayPriority: 80,
        ),
    ];

    if (chart != null && zoneEvidence != null) {
      if (chart.breakLevel != null) {
        out.add(ChartAnnotation(
          id: '${signal.id}_break',
          type: chart.breakLabel == 'CHoCH'
              ? AnnotationType.smcChoch
              : AnnotationType.smcBos,
          timeframe: chart.timeframeLabel,
          startTime: from,
          endTime: to,
          priceLow: chart.breakLevel,
          priceHigh: chart.breakLevel,
          confidence: zoneEvidence.confidence,
          evidenceId: zoneEvidence.id,
          detectorVersion: detectorVersion,
          label: chart.breakLabel ?? 'Слом структуры',
          displayPriority: 70,
        ));
      }
      for (var i = 0; i < chart.zones.length; i++) {
        final zone = chart.zones[i];
        out.add(ChartAnnotation(
          id: '${signal.id}_zone$i',
          type: AnnotationType.smcFvg,
          timeframe: chart.timeframeLabel,
          startTime: from,
          endTime: to,
          priceLow: zone.from < zone.to ? zone.from : zone.to,
          priceHigh: zone.from < zone.to ? zone.to : zone.from,
          confidence: zoneEvidence.confidence,
          evidenceId: zoneEvidence.id,
          detectorVersion: detectorVersion,
          label: zone.label.isEmpty ? 'Зона дисбаланса' : zone.label,
          displayPriority: 60,
        ));
      }
    }
    return out;
  }

  /// План: уровни от скринера, объём — от бюджета риска (ТЗ §20.1).
  static TradePlan _plan(
    TradingSignal signal, {
    required RiskBudget budget,
    required double equity,
    required DateTime expiry,
  }) {
    final priceRisk = signal.priceRisk;
    // Стоимость пункта восстанавливается из самого сигнала: unitRisk — это
    // расстояние до стопа, уже пересчитанное в рубли. Отдельный справочник
    // инструментов здесь не нужен и был бы вторым источником истины.
    final valuePerPoint = priceRisk <= 0 ? 0.0 : signal.unitRisk / priceRisk;
    final riskBudgetRubles = equity * budget.percent / 100;
    final quantity = PlanSizing.quantity(
      riskBudgetRubles: riskBudgetRubles,
      priceRisk: priceRisk,
      valuePerPoint: valuePerPoint,
      lotSize: 1,
    );
    final actualRisk = PlanSizing.riskOf(
      quantity: quantity,
      priceRisk: priceRisk,
      valuePerPoint: valuePerPoint,
    );

    // Зона входа: скринер даёт одну цену, ширину задаёт допуск исполнения.
    // Ноль вместо ширины означал бы, что войти можно только по точной цене.
    final halfWidth = priceRisk * 0.1;

    return TradePlan(
      instrumentId: signal.symbol,
      direction: signal.direction,
      orderType:
          signal.entryIsStop ? PlanOrderType.stopLimit : PlanOrderType.limit,
      entryLow: signal.entry - halfWidth,
      entryHigh: signal.entry + halfWidth,
      maxSlippagePercent: 0.15,
      stop: signal.stopLoss,
      targets: [
        for (final tp in signal.takeProfits)
          PlanTarget(
            name: tp.label,
            price: tp.price,
            fraction: tp.sharePercent / 100,
            afterFill: _afterFill(tp.index),
          ),
      ],
      quantity: quantity,
      lotSize: 1,
      valuePerPoint: valuePerPoint,
      riskRubles: actualRisk,
      riskPercent: equity <= 0 ? 0 : actualRisk / equity * 100,
      marginEstimate: quantity * signal.entry.abs() * valuePerPoint * 0.15,
      expiry: expiry,
      strategyVersion: signal.strategyId ?? detectorVersion,
      invalidation: [
        if (signal.invalidationPrice != null)
          'Закрытие за ${_price(signal.invalidationPrice!, signal.priceDecimals)} '
              'ломает замысел',
      ],
    );
  }

  static String _afterFill(int index) => switch (index) {
        1 => 'стоп в безубыток с учётом комиссий',
        2 => 'трейлинг по структуре 1H',
        _ => 'ручной разбор по политике',
      };

  /// Оценка по ТЗ §14.2 из блоков скринера и параметров плана.
  static QualityScore _score(
    TradingSignal signal, {
    required TradePlan plan,
    required List<Evidence> evidence,
    double? turnover,
    EventRisk? event,
  }) {
    // Блоки скринера, сведённые к факторам ТЗ. Price Action и RSI попадают в
    // один фактор — усредняем, а не складываем: иначе триггер получит двойной
    // вес и оценка перестанет соответствовать таблице.
    final byFactor = <ScoreFactor, List<SignalFactor>>{};
    final unmapped = <String>[];
    for (final f in signal.factors) {
      if (secondaryOnly.contains(f.name)) continue;
      final factor = factorOf[f.name];
      if (factor == null) {
        unmapped.add(f.name);
        continue;
      }
      byFactor.putIfAbsent(factor, () => []).add(f);
    }

    final contributions = <FactorContribution>[];
    for (final entry in byFactor.entries) {
      final parts = entry.value;
      final fraction =
          parts.fold(0.0, (s, f) => s + f.fraction) / parts.length;
      final evidenceId = evidence
          .where((e) => e.summary == parts.first.name)
          .firstOrNull
          ?.id;
      contributions.add(FactorContribution(
        factor: entry.key,
        fraction: fraction,
        note: parts.map((p) => p.text).join(' · '),
        evidenceId: evidenceId,
      ));
    }

    // R/R: цель ТЗ — 1:2. Всё, что выше, засчитывается полностью.
    final rr = plan.rrToSecondTarget;
    contributions.add(FactorContribution(
      factor: ScoreFactor.riskReward,
      fraction: (rr / TradePlan.targetRr).clamp(0.0, 1.0),
      note: 'до ${plan.targets.length >= 2 ? plan.targets[1].name : 'цели'} '
          '${rr.toStringAsFixed(2).replaceAll('.', ',')}R',
    ));

    // Ликвидность. Порог — оборот, при котором наш объём не заметен в стакане.
    contributions.add(turnover == null
        ? const FactorContribution(
            factor: ScoreFactor.liquidity,
            fraction: 0,
            note: 'оборот не измерен — фактор не засчитан',
          )
        : FactorContribution(
            factor: ScoreFactor.liquidity,
            fraction: (turnover / _liquidTurnover).clamp(0.0, 1.0),
            note: 'оборот ${(turnover / 1e6).toStringAsFixed(1).replaceAll('.', ',')} млн ₽',
          ));

    // Событийный риск: чистое окно — полный балл, запрет входа — ноль.
    contributions.add(switch (event) {
      null => const FactorContribution(
          factor: ScoreFactor.eventRisk,
          fraction: 1,
          note: 'событий в окне сделки нет',
        ),
      final e when e.blocksEntry => FactorContribution(
          factor: ScoreFactor.eventRisk,
          fraction: 0,
          note: '${e.title}: вход запрещён политикой',
        ),
      final e => FactorContribution(
          factor: ScoreFactor.eventRisk,
          fraction: e.impact == EventImpact.high ? 0.3 : 0.7,
          note: '${e.title}: ${e.policy}',
        ),
    });

    // Факторы, которых расчёт не дал вовсе. Пропустить их значило бы
    // показать оценку из семи факторов по шкале из восьми: число выглядело
    // бы низким без объяснения, а причина осталась бы невидимой.
    final covered = {for (final c in contributions) c.factor};
    for (final factor in ScoreFactor.values) {
      if (covered.contains(factor)) continue;
      contributions.add(FactorContribution(
        factor: factor,
        fraction: 0,
        note: unmapped.isEmpty
            ? 'расчёт не дал этого фактора'
            : 'расчёт не дал этого фактора; неучтённые блоки: '
                '${unmapped.join(", ")}',
      ));
    }

    return QualityScore(contributions: contributions);
  }

  /// Оборот, выше которого ликвидность считается достаточной, ₽ в день.
  static const _liquidTurnover = 500e6;

  static List<DataQualityFlag> _dataFlags(
    TradingSignal signal, {
    double? turnover,
  }) =>
      [
        if (turnover == null) DataQualityFlag.lowLiquidity,
        if (signal.chart == null) DataQualityFlag.gap,
      ];

  /// Цена с разделителем разрядов: «87 300», а не «87300».
  ///
  /// Число без разделителя на пять знаков читается с запинкой, а условие
  /// инвалидации читают в момент, когда думать некогда.
  static String _price(double value, int decimals) {
    final fixed = value.toStringAsFixed(decimals);
    final dot = fixed.indexOf('.');
    final whole = dot < 0 ? fixed : fixed.substring(0, dot);
    final rest = dot < 0 ? '' : ',${fixed.substring(dot + 1)}';
    final grouped = whole.replaceAllMapped(
        RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => '\u00A0');
    return '$grouped$rest';
  }
}
