/// Аналитический слой графика (ТЗ §10.1).
enum ChartLayer {
  candles('Candles', 'Свечи и текущая цена'),
  trend('Trend', 'Трендовые линии, канал, HH/HL или LH/LL'),
  wyckoff('Wyckoff', 'Диапазон, фазы A–E, Spring/UTAD, SOS/SOW'),
  smc('SMC', 'Order block, FVG, пул ликвидности, sweep, BOS/CHoCH'),
  priceAction('Price Action', 'Поглощение, пин-бар, отказ, ложный пробой'),
  levels('Levels', 'Зона входа, стоп, цели, инвалидация'),
  volume('Volume', 'Объём, относительный объём, кульминация'),
  openInterest('Open interest', 'Открытый интерес и его дельта'),
  events('Events', 'Метки событий и окна риска');

  const ChartLayer(this.label, this.meaning);

  final String label;
  final String meaning;

  /// Слой, который нельзя выключить: без свечей график не график.
  bool get alwaysOn => this == ChartLayer.candles;
}

/// Тип аннотации на графике.
enum AnnotationType {
  trendline('Трендовая'),
  channel('Канал'),
  wyckoffRange('Диапазон Вайкоффа'),
  wyckoffSpring('Spring'),
  wyckoffUtad('UTAD'),
  wyckoffSos('SOS'),
  wyckoffSow('SOW'),
  smcOrderBlock('Order block'),
  smcFvg('FVG'),
  smcLiquidityPool('Пул ликвидности'),
  smcSweep('Sweep'),
  smcBos('BOS'),
  smcChoch('CHoCH'),
  paEngulfing('Поглощение'),
  paPinBar('Пин-бар'),
  paRejection('Отказ'),
  paFailedBreakout('Ложный пробой'),
  levelEntry('Зона входа'),
  levelStop('Стоп'),
  levelTarget('Цель'),
  levelInvalidation('Инвалидация'),
  volumeClimax('Кульминация объёма'),
  oiBuildup('Набор позиции'),
  eventMarker('Событие'),

  /// Момент, в который движок посчитал идею.
  ///
  /// Без него график не отвечает на первый же вопрос, который задаёт
  /// владелец: на какой свече это появилось. Все уровни плана нарисованы
  /// от края до края, и понять, что было «до», а что «после», нельзя —
  /// а именно от этого зависит, отработал сетап или ещё нет.
  planCreated('Идея');

  const AnnotationType(this.label);

  final String label;

  /// К какому слою относится тип. Слой выключен — аннотация скрыта.
  ChartLayer get layer => switch (this) {
        AnnotationType.trendline || AnnotationType.channel => ChartLayer.trend,
        AnnotationType.wyckoffRange ||
        AnnotationType.wyckoffSpring ||
        AnnotationType.wyckoffUtad ||
        AnnotationType.wyckoffSos ||
        AnnotationType.wyckoffSow =>
          ChartLayer.wyckoff,
        AnnotationType.smcOrderBlock ||
        AnnotationType.smcFvg ||
        AnnotationType.smcLiquidityPool ||
        AnnotationType.smcSweep ||
        AnnotationType.smcBos ||
        AnnotationType.smcChoch =>
          ChartLayer.smc,
        AnnotationType.paEngulfing ||
        AnnotationType.paPinBar ||
        AnnotationType.paRejection ||
        AnnotationType.paFailedBreakout =>
          ChartLayer.priceAction,
        AnnotationType.planCreated ||
        AnnotationType.levelEntry ||
        AnnotationType.levelStop ||
        AnnotationType.levelTarget ||
        AnnotationType.levelInvalidation =>
          ChartLayer.levels,
        AnnotationType.volumeClimax => ChartLayer.volume,
        AnnotationType.oiBuildup => ChartLayer.openInterest,
        AnnotationType.eventMarker => ChartLayer.events,
      };
}

/// Разметка на графике (ТЗ §10.6).
///
/// Каждая метка обязана нести confidence, таймфрейм, границы по времени и
/// цене и версию детектора. Без этого нельзя ни проверить разметку задним
/// числом, ни понять, почему она изменилась после обновления.
class ChartAnnotation {
  const ChartAnnotation({
    required this.id,
    required this.type,
    required this.timeframe,
    required this.startTime,
    required this.endTime,
    required this.confidence,
    required this.evidenceId,
    required this.detectorVersion,
    required this.label,
    this.priceLow,
    this.priceHigh,
    this.displayPriority = 50,
  });

  final String id;
  final AnnotationType type;
  final String timeframe;
  final DateTime startTime;
  final DateTime endTime;

  /// Диапазон цен. null — метка привязана только ко времени (например, событие).
  final double? priceLow;
  final double? priceHigh;

  /// Уверенность детектора 0…1. Ниже порога слой не показывается (§10.2).
  final double confidence;

  /// Доказательство, на которое ссылается эта метка. Связь обязательна:
  /// ТЗ §9.1 запрещает рисовать слой, не участвовавший в оценке.
  final String evidenceId;

  final String detectorVersion;
  final String label;

  /// Приоритет отрисовки при наложении (§10.7).
  final int displayPriority;

  ChartLayer get layer => type.layer;

  Map<String, dynamic> toJson() => {
        'id': id,
        'type': type.name,
        'timeframe': timeframe,
        'startTime': startTime.toIso8601String(),
        'endTime': endTime.toIso8601String(),
        if (priceLow != null) 'priceLow': priceLow,
        if (priceHigh != null) 'priceHigh': priceHigh,
        'confidence': confidence,
        'evidenceId': evidenceId,
        'detectorVersion': detectorVersion,
        'label': label,
        'displayPriority': displayPriority,
      };

  static ChartAnnotation fromJson(Map<String, dynamic> j) => ChartAnnotation(
        id: j['id'] as String,
        type: AnnotationType.values.firstWhere(
          (t) => t.name == j['type'],
          orElse: () => AnnotationType.levelEntry,
        ),
        timeframe: j['timeframe'] as String? ?? 'H1',
        startTime: DateTime.parse(j['startTime'] as String),
        endTime: DateTime.parse(j['endTime'] as String),
        priceLow: (j['priceLow'] as num?)?.toDouble(),
        priceHigh: (j['priceHigh'] as num?)?.toDouble(),
        confidence: (j['confidence'] as num?)?.toDouble() ?? 0,
        evidenceId: j['evidenceId'] as String? ?? '',
        detectorVersion: j['detectorVersion'] as String? ?? '',
        label: j['label'] as String? ?? '',
        displayPriority: (j['displayPriority'] as num?)?.toInt() ?? 50,
      );
}

/// Доказательство, стоящее за фактором оценки (ТЗ §9.1, §10).
///
/// Тезис идеи и разметка графика ссылаются на один и тот же Evidence: текст
/// и картинка обязаны говорить одно и то же. Если детектор не участвовал в
/// оценке, его слой на графике не рисуется вовсе.
class Evidence {
  const Evidence({
    required this.id,
    required this.kind,
    required this.summary,
    required this.detail,
    required this.confidence,
    required this.detectorVersion,
    this.annotationIds = const [],
    this.conflictsWith = const [],
  });

  final String id;

  /// Тип доказательства: market_regime, structure, wyckoff_phase,
  /// smc_context, entry_trigger, volume_confirmation, position_building,
  /// event_risk — по таблице §10.1.
  final String kind;

  /// Одна строка для карточки.
  final String summary;

  /// Развёрнутое объяснение для раскрытия.
  final String detail;

  final double confidence;
  final String detectorVersion;

  /// Метки на графике, которые подсвечиваются по тапу (§9.1).
  final List<String> annotationIds;

  /// С чем это доказательство конфликтует.
  ///
  /// ТЗ §9.1 требует показывать конфликт детекторов, а не прятать его:
  /// «Wyckoff bullish, event risk bearish» — это то, что владелец обязан
  /// увидеть до входа, а не после.
  final List<String> conflictsWith;

  bool get hasConflict => conflictsWith.isNotEmpty;
}
