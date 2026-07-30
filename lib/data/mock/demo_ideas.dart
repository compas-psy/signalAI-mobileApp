import '../../domain/enums.dart';
import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/idea/quality_score.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/signal.dart';

/// Идеи для демо-режима — данные макета, а не рынка.
///
/// Существуют ровно для одного: посмотреть интерфейс, когда движка нет под
/// рукой. Идеи считает сервер (§18), на устройстве они не собираются, поэтому
/// без адреса движка лента пуста по построению — и оценить разбор идеи не на
/// чем, даже когда он готов.
///
/// Числа здесь **выдуманы** и на рынок не ссылаются. Приложение говорит об
/// этом полосой «ДЕМО» над каждым экраном: в приложении, которое отправляет
/// заявки, выдуманный уровень входа обязан быть отличим от настоящего.
///
/// Состав подобран так, чтобы разбор был наполнен целиком: план с зоной входа
/// и тремя целями, доказательства со ссылками на разметку, оценка §15.1
/// одиннадцатью компонентами — включая неизмеренный и неприменимый, потому
/// что именно их различение экран и обязан показывать.
abstract final class DemoIdeas {
  static List<Idea> all(DateTime now) => [
        _si(now),
        _btc(now),
        _br(now),
      ];

  /// Сработавший сигнал: план подписывается прямо сейчас.
  static Idea _si(DateTime now) => Idea(
        id: 'si',
        instrumentId: 'SiU6',
        instrumentName: 'Доллар/Рубль · сент 2026',
        market: Market.forts,
        direction: Direction.long,
        strategy: SetupStrategy.trendPullback,
        strategyVersion: 'v1.0.3',
        state: IdeaState.triggered,
        score: _score(
          total: 84,
          measured: const {
            ScoreComponentKind.regimeFit: (87, '1D восходящая структура, 4H повторное накопление'),
            ScoreComponentKind.marketStructure: (91, 'Spring → SOS → LPS, удержан higher low'),
            ScoreComponentKind.setupQuality: (86, 'Откат в зону OB, объём на откате падает'),
            ScoreComponentKind.triggerQuality: (88, 'Бычье поглощение 1H с ростом объёма'),
            ScoreComponentKind.derivativesConfirmation: (74, 'Открытый интерес +6% на росте'),
            ScoreComponentKind.volumeConfirmation: (81, 'Объём входа +18% к среднему'),
            ScoreComponentKind.liquidityQuality: (93, 'Спред 1 пункт, стакан плотный'),
            ScoreComponentKind.volatilityFit: (69, 'ATR в верхней половине нормы'),
            ScoreComponentKind.levelConfluence: (77, 'OB, 0,5 отката и дневной минимум сошлись'),
            ScoreComponentKind.riskRewardQuality: (90, 'R:R до TP2 равен 2,3'),
          },
          // Неизмеренное показывается отдельно от слабого: отказ источника и
          // измеренная слабость — разные новости.
          missing: const {
            ScoreComponentKind.executionQuality: 'Проскальзывание не измерено: журнал исполнений пуст',
          },
          dataQuality: 0.94,
        ),
        createdAt: now.subtract(const Duration(minutes: 18)),
        validUntil: now.add(const Duration(minutes: 42)),
        thesis: 'На 1D сохраняется восходящая структура. На 4H цена вернулась '
            'в зону повторного накопления, сняла ликвидность под локальным '
            'минимумом и вернулась выше бычьего ордер-блока. На 1H — бычье '
            'поглощение с ростом объёма и открытого интереса.',
        timeframes: const ['1D', '4H', '1H'],
        plan: TradePlan(
          instrumentId: 'SiU6',
          direction: Direction.long,
          orderType: PlanOrderType.limit,
          entryLow: 87400,
          entryHigh: 87500,
          maxSlippagePercent: 0.05,
          stop: 86900,
          targets: const [
            PlanTarget(name: 'TP1', price: 88200, fraction: 0.4, afterFill: 'стоп в безубыток'),
            PlanTarget(name: 'TP2', price: 88700, fraction: 0.4, afterFill: 'трейл по структуре 1H'),
            PlanTarget(name: 'TP3', price: 89400, fraction: 0.2, afterFill: 'закрытие остатка'),
          ],
          quantity: 32,
          lotSize: 1,
          valuePerPoint: 1,
          riskRubles: 17600,
          riskPercent: 0.73,
          marginEstimate: 168000,
          expiry: now.add(const Duration(minutes: 42)),
          strategyVersion: 'v1.0.3',
          invalidation: const [
            'Закрытие 1H ниже 86 900',
            'Рост открытого интереса при падении цены после входа',
            'R:R до TP2 падает ниже 1:1,8',
            'Новое событие высокой важности внутри 30 минут',
          ],
        ),
        evidence: [
          _evidence('ev-regime', 'market_regime', 'Режим: 1D вверх, 4H повторное накопление',
              'Старший таймфрейм не против сделки — это условие входа, а не украшение.',
              ['si_trend']),
          _evidence('ev-structure', 'structure', 'Структура: Spring → SOS → LPS',
              'Последний higher low удержан, фаза C пройдена.', ['si_wyckoff']),
          _evidence('ev-smc', 'smc_context', 'SMC: снятие ликвидности и бычий OB',
              'Ликвидность под минимумом снята, цена вернулась выше блока.',
              ['si_ob', 'si_sweep']),
          _evidence('ev-trigger', 'entry_trigger', 'Триггер: бычье поглощение 1H',
              'Свеча закрылась выше тела предыдущей на повышенном объёме.',
              ['si_engulf']),
          _evidence('ev-volume', 'volume_confirmation', 'Объём и открытый интерес подтверждают',
              'Объём +18%, открытый интерес +6% — набор позиции, а не отскок.',
              ['si_oi']),
          _evidence('ev-event', 'event_risk', 'Событийный риск умеренный',
              'Налоговый период поддерживает рубль; вечерняя статистика США добавит волатильности.',
              const []),
        ],
        annotations: [
          _annotation('si_trend', AnnotationType.trendline, '1D', 'ev-regime', now, 86500, 89600),
          _annotation('si_wyckoff', AnnotationType.wyckoffSpring, '4H', 'ev-structure', now, 86850, 87050),
          _annotation('si_ob', AnnotationType.smcOrderBlock, '4H', 'ev-smc', now, 87380, 87520),
          _annotation('si_sweep', AnnotationType.smcSweep, '1H', 'ev-smc', now, 86880, 86960),
          _annotation('si_engulf', AnnotationType.paEngulfing, '1H', 'ev-trigger', now, 87400, 87500),
          _annotation('si_oi', AnnotationType.oiBuildup, '1H', 'ev-volume', now, null, null),
        ],
        eventRisk: EventRisk(
          title: 'Статистика США по занятости',
          at: now.add(const Duration(hours: 5, minutes: 30)),
          impact: EventImpact.mid,
          policy: 'риск на сделку урезан вдвое в окне ±30 минут',
        ),
        dataFlags: const [DataQualityFlag.partialBar],
      );

  /// Зона известна, триггера ещё нет: подтверждать нечего.
  static Idea _btc(DateTime now) => Idea(
        id: 'btc',
        instrumentId: 'BTCUSDT',
        instrumentName: 'Биткоин к доллару · бессрочный',
        market: Market.crypto,
        direction: Direction.long,
        strategy: SetupStrategy.breakoutRetest,
        strategyVersion: 'v1.0.3',
        state: IdeaState.ready,
        score: _score(
          total: 78,
          measured: const {
            ScoreComponentKind.regimeFit: (76, '4H накопление после пробоя'),
            ScoreComponentKind.marketStructure: (84, 'Higher low удержан, диапазон пройден вверх'),
            ScoreComponentKind.setupQuality: (79, 'Ретест верхней границы сверху'),
            ScoreComponentKind.triggerQuality: (48, 'Ждём закрытие выше 118 400'),
            ScoreComponentKind.derivativesConfirmation: (86, 'Фандинг отрицательный, открытый интерес растёт'),
            ScoreComponentKind.volumeConfirmation: (72, 'Объём на откате падает'),
            ScoreComponentKind.liquidityQuality: (95, 'Ликвидность высокая'),
            ScoreComponentKind.volatilityFit: (71, 'ATR в норме'),
            ScoreComponentKind.levelConfluence: (74, 'Граница диапазона и бычий OB'),
            ScoreComponentKind.riskRewardQuality: (81, 'R:R до TP2 равен 2,7'),
            ScoreComponentKind.executionQuality: (88, 'Проскальзывание в пределах 0,02%'),
          },
          dataQuality: 1.0,
        ),
        createdAt: now.subtract(const Duration(hours: 1)),
        validUntil: now.add(const Duration(hours: 3, minutes: 12)),
        thesis: 'На 4H диапазон пройден вверх, цена вернулась к его верхней '
            'границе и держится над ней. На 1H идёт ретест бычьего '
            'ордер-блока; нужен слабый откат и закрытие выше 118 400. '
            'Фандинг отрицательный при растущем открытом интересе — рынок '
            'платит за короткие позиции против движения.',
        timeframes: const ['1D', '4H', '1H'],
        plan: TradePlan(
          instrumentId: 'BTCUSDT',
          direction: Direction.long,
          orderType: PlanOrderType.stopLimit,
          entryLow: 118100,
          entryHigh: 118400,
          maxSlippagePercent: 0.1,
          stop: 115800,
          targets: const [
            PlanTarget(name: 'TP1', price: 121400, fraction: 0.4, afterFill: 'стоп в безубыток'),
            PlanTarget(name: 'TP2', price: 124800, fraction: 0.4, afterFill: 'трейл по структуре 1H'),
            PlanTarget(name: 'TP3', price: 129000, fraction: 0.2, afterFill: 'закрытие остатка'),
          ],
          quantity: 9,
          lotSize: 1,
          valuePerPoint: 0.78,
          riskRubles: 17200,
          riskPercent: 0.72,
          marginEstimate: 96000,
          expiry: now.add(const Duration(hours: 3, minutes: 12)),
          strategyVersion: 'v1.0.3',
          invalidation: const [
            'Закрепление 1H ниже 115 800',
            'Снижение открытого интереса более чем на 8% до входа',
            'Импульсный возврат в диапазон вниз',
          ],
        ),
        evidence: [
          _evidence('ev-btc-regime', 'market_regime', 'Режим: 4H накопление после пробоя',
              'Диапазон пройден вверх, предложение исчерпано.', ['btc_range']),
          _evidence('ev-btc-structure', 'structure', 'Структура: higher low над границей',
              'Верхняя граница стала опорой, а не сопротивлением.', ['btc_break']),
          _evidence('ev-btc-trigger', 'entry_trigger', 'Триггера ещё нет',
              'Нужно закрытие 1H выше 118 400 — до него идея остаётся ожиданием.',
              const []),
          _evidence('ev-btc-oi', 'position_building', 'Фандинг отрицательный при росте интереса',
              'Рынок платит за короткие позиции против движения.',
              ['btc_oi']),
        ],
        annotations: [
          _annotation('btc_range', AnnotationType.wyckoffRange, '4H', 'ev-btc-regime', now, 115800, 118400),
          _annotation('btc_break', AnnotationType.smcBos, '4H', 'ev-btc-structure', now, 118100, 118400),
          _annotation('btc_oi', AnnotationType.oiBuildup, '1H', 'ev-btc-oi', now, null, null),
        ],
        eventRisk: EventRisk(
          title: 'Ставка ФРС',
          at: now.add(const Duration(hours: 3)),
          impact: EventImpact.high,
          policy: 'вход запрещён за 30 минут до публикации',
          blocksEntry: true,
        ),
      );

  /// Наблюдение: контекст есть, плана нет — и это видно на экране.
  ///
  /// Идентификатор совпадает с сигналом демо-дайджеста намеренно: разбор
  /// ищет идею по идентификатору сигнала, и разойдись они — экран показал бы
  /// шапку одного инструмента с планом другого.
  static Idea _br(DateTime now) => Idea(
        id: 'br',
        instrumentId: 'BR-9.26',
        instrumentName: 'Нефть Brent · сент 2026',
        market: Market.forts,
        direction: Direction.short,
        strategy: SetupStrategy.breakoutRetest,
        strategyVersion: 'v1.0.3',
        state: IdeaState.watch,
        score: _score(
          total: 71,
          measured: const {
            ScoreComponentKind.regimeFit: (72, '1D нейтрально-вниз'),
            ScoreComponentKind.marketStructure: (66, 'Распределение, импульса вниз ещё не было'),
            ScoreComponentKind.setupQuality: (70, 'Сжатие над нижней границей'),
            ScoreComponentKind.triggerQuality: (35, 'Триггер отсутствует'),
            ScoreComponentKind.volumeConfirmation: (78, 'Объём на отскоках снижается'),
            ScoreComponentKind.liquidityQuality: (61, 'Ликвидность средняя'),
            ScoreComponentKind.volatilityFit: (74, 'ATR сжат'),
            ScoreComponentKind.levelConfluence: (80, 'Пул ликвидности под 81,90'),
            ScoreComponentKind.riskRewardQuality: (92, 'Потенциал R:R 2,9'),
            ScoreComponentKind.executionQuality: (70, 'Спред шире обычного'),
          },
          // Открытого интереса по этому контракту приложение не получает —
          // это не изъян идеи, и вес компонента ушёл остальным.
          notApplicable: const {
            ScoreComponentKind.derivativesConfirmation:
                'Открытый интерес по контракту не публикуется',
          },
          dataQuality: 1.0,
        ),
        createdAt: now.subtract(const Duration(hours: 6)),
        validUntil: now.add(const Duration(days: 1, hours: 4)),
        thesis: 'Цена сжимается над нижней границей распределения. Объём на '
            'отскоках снижается, продавец не уходит. Идея остаётся '
            'наблюдением: импульса ниже 81,90 и подтверждённого ретеста ещё '
            'не было, поэтому плана сделки у неё нет.',
        timeframes: const ['1D', '4H'],
        plan: null,
        evidence: [
          _evidence('ev-br-regime', 'market_regime', 'Режим: 1D нейтрально-вниз',
              'Тренда против сделки нет, но и импульса за неё пока тоже.',
              ['br_range']),
          _evidence('ev-br-liquidity', 'smc_context', 'Пул ликвидности под 81,90',
              'Цель пробоя видна; сам пробой ещё не состоялся.', ['br_pool']),
        ],
        annotations: [
          _annotation('br_range', AnnotationType.wyckoffRange, '1D', 'ev-br-regime', now, 81.90, 83.40),
          _annotation('br_pool', AnnotationType.smcLiquidityPool, '4H', 'ev-br-liquidity', now, 81.40, 81.90),
        ],
        dataFlags: const [DataQualityFlag.lowLiquidity],
      );

  /// Свечи для графика демо-идеи.
  ///
  /// Считаются от её же уровней, а не тянутся с биржи. Настоящие цены под
  /// выдуманным планом — худший из вариантов: зона входа и стоп улетают за
  /// пределы графика, и картинка выглядит сломанной, хотя сломаны данные.
  ///
  /// Ряд детерминированный: одна и та же идея рисуется одинаково при каждом
  /// открытии. Случайность здесь означала бы график, который меняется сам по
  /// себе между двумя взглядами.
  static SignalChart? chartFor(Idea idea) {
    final plan = idea.plan;
    // Идея без плана — наблюдение: рисовать зону входа и стоп нечем.
    final low = plan?.stop ?? 0;
    final high = plan == null ? 0.0 : plan.targets.last.price;
    if (plan == null || low <= 0 || high <= low) return null;

    final span = high - low;
    final candles = <ChartCandle>[];
    final start = DateTime(2026, 7, 29, 10);
    var price = plan.direction.isLong ? low + span * 0.28 : high - span * 0.28;
    for (var i = 0; i < 64; i++) {
      // Пила с уклоном к зоне входа: ряд узнаваемо «рыночный», но полностью
      // предсказуемый — ни одного случайного числа.
      final wave = ((i * 37) % 17 - 8) / 8;
      final drift = (plan.direction.isLong ? 1 : -1) * span * 0.004;
      final open = price;
      price = (price + drift + wave * span * 0.018).clamp(low * 0.995, high * 1.005);
      final close = price;
      final wick = span * 0.012;
      candles.add(ChartCandle(
        open,
        (open > close ? open : close) + wick,
        (open < close ? open : close) - wick,
        close,
        start.add(Duration(hours: i * 4)),
      ));
    }
    return SignalChart(
      timeframeLabel: idea.timeframes.length >= 2 ? idea.timeframes[1] : '4h',
      candles: candles,
    );
  }

  static Evidence _evidence(
    String id,
    String kind,
    String summary,
    String detail,
    List<String> annotationIds,
  ) =>
      Evidence(
        id: id,
        kind: kind,
        summary: summary,
        detail: detail,
        confidence: 0.8,
        detectorVersion: 'demo',
        annotationIds: annotationIds,
      );

  static ChartAnnotation _annotation(
    String id,
    AnnotationType type,
    String timeframe,
    String evidenceId,
    DateTime now,
    double? low,
    double? high,
  ) =>
      ChartAnnotation(
        id: id,
        type: type,
        timeframe: timeframe,
        startTime: now.subtract(const Duration(hours: 8)),
        endTime: now,
        priceLow: low,
        priceHigh: high,
        confidence: 0.8,
        evidenceId: evidenceId,
        detectorVersion: 'demo',
        label: type.label,
      );

  /// Оценка §15.1 с перераспределением веса.
  ///
  /// Вес неприменимого компонента уходит остальным — ровно так, как это
  /// делает движок. Считать иначе значило бы показать знаменатель, которого
  /// в этой оценке не существует.
  static QualityScore _score({
    required double total,
    required Map<ScoreComponentKind, (int, String)> measured,
    Map<ScoreComponentKind, String> missing = const {},
    Map<ScoreComponentKind, String> notApplicable = const {},
    double dataQuality = 1.0,
  }) {
    final applicable = ScoreComponentKind.values
        .where((k) => !notApplicable.containsKey(k))
        .toList();
    final applicableWeight =
        applicable.fold(0.0, (sum, k) => sum + k.weight);
    final contributions = <ScoreContribution>[];
    for (final kind in ScoreComponentKind.values) {
      final effective =
          notApplicable.containsKey(kind) ? 0.0 : kind.weight / applicableWeight;
      if (notApplicable.containsKey(kind)) {
        contributions.add(ScoreContribution(
          kind: kind,
          value: 0,
          effectiveWeight: 0,
          points: 0,
          status: MeasurementStatus.notApplicable,
          detail: notApplicable[kind]!,
        ));
      } else if (missing.containsKey(kind)) {
        contributions.add(ScoreContribution(
          kind: kind,
          value: 0,
          effectiveWeight: effective,
          points: 0,
          status: MeasurementStatus.missing,
          detail: missing[kind]!,
        ));
      } else {
        final (value, detail) = measured[kind]!;
        contributions.add(ScoreContribution(
          kind: kind,
          value: value.toDouble(),
          effectiveWeight: effective,
          points: value * effective,
          detail: detail,
        ));
      }
    }
    return QualityScore(
      contributions: contributions,
      total: total,
      dataQuality: dataQuality,
    );
  }
}
