import '../../domain/enums.dart';
import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/idea/paper_position.dart';
import '../../domain/idea/quality_score.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/signal.dart';
import '../../domain/portfolio/package.dart';

/// Разбор контракта §18: идея приходит с сервера целиком.
///
/// Здесь **не считается ничего**. Оценка, вероятность, размер позиции и
/// разметка приезжают посчитанными; задача этого файла — перенести их в
/// доменную модель, ничего по дороге не выдумав.
///
/// Это принципиально. ТЗ §27 требует, чтобы Paper и Live пользовались одной
/// бизнес-логикой; вторая реализация тех же формул на Dart разошлась бы с
/// серверной на третьем округлении, и владелец увидел бы у одной идеи два
/// разных балла — не зная, какому верить. Прежний мост `idea_mapper` именно
/// этим и занимался: набивал восемь факторов ТЗ v2 из шести факторов
/// легаси-скринера, подставляя ноль там, где измерять было нечем.
///
/// Поля, которых в ответе нет, становятся **отсутствующими**, а не нулевыми.
/// Ноль вместо пропуска — это утверждение о рынке, которого никто не делал.
abstract final class EngineContract {
  /// Сигнал для экранов, собранный из идеи движка.
  ///
  /// Разбор идеи устроен вокруг [TradingSignal]: шапка, график и уровни берут
  /// данные оттуда. Сигналы приходят с дайджеста, а идеи — с движка, и
  /// пересечения по идентификаторам между ними нет. Из-за этого нажатие по
  /// карточке движка либо не открывало ничего (дайджест пуст), либо
  /// открывало **чужой** инструмент — тот, что оказался в дайджесте первым.
  /// Второе хуже: экран показывал тикер, цену и уровни другой сделки.
  ///
  /// Здесь идея переводится в сигнал один в один, без выдумывания. Чего у
  /// идеи нет — цены последней сделки, дневного изменения — остаётся пустым,
  /// и экран его не рисует.
  static TradingSignal signalFrom(Idea idea) {
    final plan = idea.plan;
    final targets = plan?.targets ?? const <PlanTarget>[];
    final decimals = _decimalsFor([
      if (plan != null) ...[plan.entryLow, plan.entryHigh, plan.stop],
      for (final t in targets) t.price,
    ]);
    return TradingSignal(
      id: idea.id,
      symbol: symbolOf(idea.instrumentId),
      name: idea.instrumentName,
      market: idea.market,
      direction: idea.direction,
      horizon: Horizon.swing,
      horizonLabel: idea.timeframes.join(' / '),
      score: idea.score.value,
      entry: plan?.entry ?? 0,
      stopLoss: plan?.stop ?? 0,
      takeProfits: [
        for (var i = 0; i < targets.length; i++)
          TakeProfit(
            index: i + 1,
            price: targets[i].price,
            sharePercent: (targets[i].fraction * 100).round(),
          ),
      ],
      priceDecimals: decimals,
      riskReward: plan == null
          ? ''
          : plan.rrToSecondTarget.toStringAsFixed(1).replaceAll('.', ','),
      chips: const [],
      note: idea.thesis,
      factors: const [],
      events: const [],
      unitRisk: plan?.riskPerUnit ?? 0,
      unitRiskLabel: '',
      unitMultiplier: 1,
      unitDecimals: 0,
      unitName: '',
      // Котировки идея не несёт: их место остаётся пустым, а не заполняется
      // нулём. Ноль на месте цены читается как обвал.
      lastPrice: '',
      changeLabel: '',
      changeUp: true,
      status: idea.state == IdeaState.active
          ? SignalStatus.working
          : (idea.state.isTerminal
              ? SignalStatus.expired
              : (idea.canApprovePaper
                  ? SignalStatus.proposed
                  : SignalStatus.watch)),
      validUntil: idea.validUntil,
      strategyId: idea.strategy.name,
      entryIsStop: plan?.orderType == PlanOrderType.stopLimit,
    );
  }

  /// Тикер из канонического идентификатора: «CRYPTO:PERP:BTCUSDT» → «BTCUSDT».
  ///
  /// Движок называет инструмент площадкой, классом и кодом. Целиком эта
  /// строка в заголовок карточки не помещается и обрезается на «CRYPTO:PER…»
  /// — по такой подписи нельзя понять, о каком активе идея.
  /// Разбор advisory-only ребаланса выбранного пакета.
  static PortfolioRebalance portfolioRebalance(Map<String, dynamic> json) {
    final actions = <PortfolioRebalanceAction>[];
    final rawActions = json['actions'];
    if (rawActions is List) {
      for (final raw in rawActions) {
        if (raw is! Map<String, dynamic>) continue;
        actions.add(
          PortfolioRebalanceAction(
            instrumentId: '${raw['instrument_id'] ?? ''}',
            symbol: '${raw['symbol'] ?? ''}',
            side: '${raw['side'] ?? ''}',
            targetWeight: _num(raw['target_weight']),
            actualWeight: _num(raw['actual_weight']),
            amountRub: _num(raw['amount_rub']),
            reason: '${raw['reason'] ?? ''}',
            economicsStatus: '${raw['economics_status'] ?? 'UNKNOWN'}',
            actionable: raw['actionable'] == true,
            orderQuantity: _numOrNull(raw['order_quantity']),
            orderNotionalRub: _numOrNull(raw['order_notional_rub']),
            estimatedCostsRub: _numOrNull(raw['estimated_costs_rub']),
            estimatedTaxRub: _numOrNull(raw['estimated_tax_rub']),
            brokerFinalCostsRub: _numOrNull(raw['broker_final_costs_rub']),
            brokerFinalTaxRub: _numOrNull(raw['broker_final_tax_rub']),
            economicsProvenance: _stringMap(raw['economics_provenance']),
            economicsBlockers: _strings(raw['economics_blockers']),
          ),
        );
      }
    }
    return PortfolioRebalance(
      modelId: '${json['model_id'] ?? ''}',
      needed: json['needed'] == true,
      urgent: json['urgent'] == true,
      reason: '${json['reason'] ?? ''}',
      maxDrift: _num(json['max_drift']),
      totalValue: _num(json['total_value']),
      actions: actions,
      actionable: json['actionable'] == true,
      economicsStatus: '${json['economics_status'] ?? 'UNKNOWN'}',
      estimatedCostsRub: _numOrNull(json['estimated_costs_rub']),
      estimatedTaxRub: _numOrNull(json['estimated_tax_rub']),
      brokerFinalCostsRub: _numOrNull(json['broker_final_costs_rub']),
      brokerFinalTaxRub: _numOrNull(json['broker_final_tax_rub']),
    );
  }

  static String symbolOf(String instrumentId) {
    final cut = instrumentId.lastIndexOf(':');
    return cut < 0 ? instrumentId : instrumentId.substring(cut + 1);
  }

  /// Человеческое имя инструмента, когда движок его не прислал.
  ///
  /// «BTCUSDT» и «SIU6» — это коды, а решение принимает человек. Словарь
  /// покрывает вселенную движка; незнакомый код остаётся кодом — придумывать
  /// имя хуже, чем не назвать.
  static String humanName(String ticker) {
    final upper = ticker.toUpperCase();
    for (final entry in _names.entries) {
      if (upper.startsWith(entry.key)) return entry.value;
    }
    return '';
  }

  static String _nameOf(String fromServer, String instrumentId) {
    final ticker = symbolOf(instrumentId);
    if (fromServer.isNotEmpty && fromServer != ticker) return fromServer;
    return humanName(ticker);
  }

  static const _names = <String, String>{
    'BTC': 'Биткоин · бессрочный',
    'ETH': 'Эфириум · бессрочный',
    'SOL': 'Солана · бессрочный',
    'XRP': 'XRP · бессрочный',
    'TON': 'Тонкоин · бессрочный',
    'BNB': 'BNB · бессрочный',
    'DOGE': 'Доджкоин · бессрочный',
    'SI': 'Доллар/Рубль · фьючерс',
    'CNY': 'Юань/Рубль · фьючерс',
    'BR': 'Нефть Brent · фьючерс',
    'NG': 'Природный газ · фьючерс',
    'GOLD': 'Золото · фьючерс',
    'GLD': 'Золото · фьючерс',
    'SILV': 'Серебро · фьючерс',
    'MX': 'Индекс МосБиржи · фьючерс',
    'MIX': 'Индекс МосБиржи · фьючерс',
    'RTS': 'Индекс РТС · фьючерс',
    'SBRF': 'Сбербанк · фьючерс',
    'GAZR': 'Газпром · фьючерс',
  };

  /// Сколько знаков после запятой у цен инструмента.
  ///
  /// Считается по самим ценам, а не задаётся константой: у фьючерса на доллар
  /// знаков нет, у нефти два, у крипты бывает больше. Округлить чужую цену до
  /// целых значит показать не ту цену.
  /// Сравнение идёт с допуском, а не точным равенством. Сервер отдаёт цены
  /// строками Decimal («1849.100000000000»), при разборе в double они
  /// становятся 1849.0999999999999 — точная проверка не сходилась ни на
  /// одном знаке и всегда давала максимум. Отсюда «1 858,1200» вместо
  /// «1 858,12»: четыре знака там, где значащих два.
  static int _decimalsFor(List<double> prices) {
    if (prices.isEmpty) return 0;
    final needed = <int>[for (final price in prices) _decimalsOf(price)]..sort();
    // Берётся не максимум, а середина. Максимум означает, что ОДНА цена
    // задаёт вид всех остальных: сервер прислал границу зоны входа
    // неокруглённой (1874.023606), и весь экран идеи стал показывать шесть
    // знаков — «1 858,120000» вместо «1 858,12». Сервер это уже чинит
    // округлением к шагу цены, но идеи, посчитанные до того, живут своим
    // сроком, и читать их владельцу приходится сейчас.
    //
    // Медиана устойчива к одному выбросу и при этом не занижает точность,
    // когда цены действительно дробные: у крипты все шесть знаков значащие,
    // и медиана даст те же шесть.
    return needed[needed.length ~/ 2];
  }

  /// Сколько знаков после запятой нужно этой цене.
  ///
  /// Сравнение с допуском, а не точное: сервер отдаёт цены строками Decimal
  /// («1849.100000000000»), в double это 1849.0999999999999, и точная
  /// проверка не сходится ни на одном знаке.
  static int _decimalsOf(double price) {
    final scale = price.abs() < 1 ? 1.0 : price.abs();
    for (var d = 0; d <= 6; d++) {
      final factor = _pow10(d);
      final rounded = (price * factor).roundToDouble() / factor;
      if ((price - rounded).abs() <= 1e-9 * scale) return d;
    }
    return 6;
  }

  static double _pow10(int n) {
    var result = 1.0;
    for (var i = 0; i < n; i++) {
      result *= 10;
    }
    return result;
  }

  /// Идея из ответа `/api/v1/ideas/{id}`.
  ///
  /// [summaryOnly] — ответ ленты, где нет ни плана, ни разбора: карточка
  /// показывается, но открыть её без подробностей нельзя.
  static Idea idea(
    Map<String, dynamic> j, {
    String? instrumentName,
    IdeaReadiness? readiness,
    bool? actionable,
  }) {
    final id = j['id'] as String;
    final direction = _direction(j['direction'] as String?);
    final signalTime = _time(j['signal_time']) ?? DateTime.now();
    final expiresAt =
        _time(j['expires_at']) ?? signalTime.add(const Duration(days: 1));

    final score = j['score_breakdown'] is Map<String, dynamic>
        ? QualityScore.fromJson(j['score_breakdown'] as Map<String, dynamic>)
        : QualityScore(
            contributions: const [],
            total: _num(j['score']),
          );

    final explanation = j['explanation'] as Map<String, dynamic>? ?? const {};

    final parsedReadiness = readiness ??
        IdeaReadiness.tryParse(j['readiness'] as String?) ??
        IdeaReadiness.tryParse(j['quality_status'] as String?);
    final parsedActionable = actionable ??
        (j['actionable'] is bool ? j['actionable'] as bool : null) ??
        parsedReadiness?.canAct;

    return Idea(
      id: id,
      instrumentId: j['instrument_id'] as String? ?? '',
      // Имя из ответа; если движок прислал тикер вместо имени (или ничего) —
      // словарь. Карточка с «BTCUSDT» дважды не отвечает на вопрос «что это».
      instrumentName: _nameOf(
        instrumentName ?? (j['symbol'] as String? ?? ''),
        j['instrument_id'] as String? ?? '',
      ),
      market: _market(j['instrument_id'] as String? ?? ''),
      direction: direction,
      strategy: _strategy(j['strategy'] as String?),
      strategyVersion: j['engine_version'] as String? ?? '',
      state: _state(j['status'] as String?),
      score: score,
      createdAt: signalTime,
      validUntil: expiresAt,
      thesis: explanation['thesis'] as String? ?? '',
      plan: _plan(j, direction: direction, expiry: expiresAt),
      timeframes: [
        if (j['context_timeframe'] != null) j['context_timeframe'] as String,
        if (j['setup_timeframe'] != null) j['setup_timeframe'] as String,
        if (j['trigger_timeframe'] != null) j['trigger_timeframe'] as String,
      ],
      evidence: evidence(j['evidence'] as List<dynamic>? ?? const []),
      annotations:
          annotations(j['annotations'] as List<dynamic>? ?? const []),
      eventRisk: _eventRisk(j['event_calendar'] as Map<String, dynamic>?),
      dataFlags: _flags(j['data_warnings'] as List<dynamic>? ?? const []),
      // Отказ движка исполнять объём переносится дословно. Раньше поле
      // `tradable` не читалось вовсе: сервер писал «идея информационная»,
      // а экран всё равно предлагал подтвердить.
      sizingBlocker: _sizingBlocker(j['sizing'] as Map<String, dynamic>?),
      closingReason: j['closing_reason'] as String? ?? '',
      readiness: parsedReadiness,
      actionable: parsedActionable,
    );
  }

  /// Причина, по которой объём нельзя исполнять. Пустая — можно.
  static String _sizingBlocker(Map<String, dynamic>? sizing) {
    if (sizing == null) return '';
    if (sizing['tradable'] != false) return '';
    final reason = sizing['not_tradable_reason'] as String? ?? '';
    return reason.isEmpty ? 'движок не считает объём исполнимым' : reason;
  }

  /// The engine owns the calendar verdict.  Missing/ambiguous data stays a
  /// blocking warning; the phone never infers a safe event window itself.
  static EventRisk? _eventRisk(Map<String, dynamic>? raw) {
    if (raw == null) return null;
    final event = raw['event'] as Map<String, dynamic>?;
    final at = DateTime.tryParse(event?['scheduled_at'] as String? ?? '');
    final status = raw['status'] as String? ?? 'UNAVAILABLE';
    final detail = raw['detail'] as String? ?? 'календарь событий недоступен';
    return EventRisk(
      title: event?['title'] as String? ?? detail,
      at: at,
      impact: EventImpact.parse(event?['impact'] as String? ?? 'high'),
      policy: raw['reason_code'] as String? ?? status,
      blocksEntry: raw['blocks_admission'] as bool? ?? true,
    );
  }

  static List<Evidence> evidence(List<dynamic> raw) {
    final out = <Evidence>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) continue;
      out.add(
        Evidence(
          id: item['id'] as String? ?? '',
          kind: item['kind'] as String? ?? '',
          summary: item['summary'] as String? ??
              (item['title'] as String? ?? ''),
          detail: item['detail'] as String? ?? '',
          confidence: _num(item['confidence']),
          detectorVersion: item['detector_version'] as String? ?? '',
          conflictsWith: (item['conflicts_with'] as List<dynamic>? ?? const [])
              .map((e) => e.toString())
              .toList(growable: false),
        ),
      );
    }
    return out;
  }

  static List<ChartAnnotation> annotations(List<dynamic> raw) {
    final out = <ChartAnnotation>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) continue;
      final type = _annotationType(item['type'] as String?);
      // Неизвестный тип метки не рисуется вовсе. Подставлять «какой-нибудь»
      // значит показать на графике не то, что нашёл детектор, — а это хуже,
      // чем не показать ничего.
      if (type == null) continue;
      final start = _time(item['start_time']);
      final end = _time(item['end_time']);
      if (start == null || end == null) continue;
      out.add(
        ChartAnnotation(
          id: item['id'] as String? ?? '',
          type: type,
          timeframe: item['timeframe'] as String? ?? '',
          startTime: start,
          endTime: end,
          priceLow: _numOrNull(item['price_low']),
          priceHigh: _numOrNull(item['price_high']),
          confidence: _num(item['confidence']),
          evidenceId: item['evidence_id'] as String? ?? '',
          detectorVersion: item['detector_version'] as String? ?? '',
          label: item['label'] as String? ?? '',
          displayPriority: (item['display_priority'] as num?)?.toInt() ?? 50,
        ),
      );
    }
    return out;
  }

  // ── Разбор частей ───────────────────────────────────────────────────────

  static TradePlan? _plan(
    Map<String, dynamic> j, {
    required Direction direction,
    required DateTime expiry,
  }) {
    final plan = j['plan'] as Map<String, dynamic>?;
    final sizing = j['sizing'] as Map<String, dynamic>?;
    if (plan == null) return null;

    final targets = <PlanTarget>[];
    // Доли §21.1 — 0,4 / 0,4 / 0,2. Они часть подписываемого плана, а не
    // настройка интерфейса.
    const fractions = [0.4, 0.4, 0.2];
    const afterFill = [
      'Закрыть 40%, стоп в безубыток с учётом комиссий',
      'Закрыть 40%, стоп по подтверждённой структуре 1H',
      'Закрыть 20% либо ручной разбор по политике',
    ];
    for (var i = 0; i < 3; i++) {
      final price = _numOrNull(plan['tp${i + 1}']);
      if (price == null) continue;
      targets.add(
        PlanTarget(
          name: 'TP${i + 1}',
          price: price,
          fraction: fractions[i],
          afterFill: afterFill[i],
        ),
      );
    }

    return TradePlan(
      instrumentId: j['instrument_id'] as String? ?? '',
      direction: direction,
      orderType: _orderType(plan['order_intent'] as String?),
      entryLow: _num(plan['entry_low']),
      entryHigh: _num(plan['entry_high']),
      entryReference: _numOrNull(plan['entry_reference']),
      maxSlippagePercent: 0.1,
      stop: _num(plan['stop']),
      targets: targets,
      // Объём не округляется до целого. Округление здесь стоило бы позиции:
      // 0,348 ETH превращались в ноль, а «28» читалось как двадцать восемь
      // монет — два миллиона рублей при депозите в один.
      quantity: _num(sizing?['quantity']),
      quantityStep: _numOrNull(sizing?['quantity_step']) ?? 1,
      quantityUnit: sizing?['quantity_unit'] as String? ?? '',
      lotSize: 1,
      valuePerPoint: _riskPerUnitToValue(sizing, plan),
      quoteCurrency: sizing?['quote_currency'] as String? ?? 'RUB',
      quoteRateRub: _numOrNull(sizing?['quote_rate_rub']),
      quoteNote: sizing?['quote_note'] as String? ?? '',
      riskRubles: _num(sizing?['risk_amount']),
      riskPercent: _num(sizing?['risk_pct']) * 100,
      marginEstimate: 0,
      expiry: expiry,
      strategyVersion: j['engine_version'] as String? ?? '',
      invalidation: [
        if ((plan['invalidation'] as String? ?? '').isNotEmpty)
          plan['invalidation'] as String,
      ],
    );
  }

  /// Стоимость пункта восстанавливается из риска на единицу, а не задаётся
  /// единицей по умолчанию.
  ///
  /// Зашитая единица — это молчаливая ошибка в деньгах: у фьючерса на доллар
  /// пункт стоит рубль, у нефти — семь с половиной, и позиция, посчитанная по
  /// единице, отличается от верной в разы.
  static double _riskPerUnitToValue(
    Map<String, dynamic>? sizing,
    Map<String, dynamic> plan,
  ) {
    final riskPerUnit = _numOrNull(sizing?['risk_per_unit']);
    final entry = _numOrNull(plan['entry_reference']) ??
        (_num(plan['entry_low']) + _num(plan['entry_high'])) / 2;
    final stop = _num(plan['stop']);
    final priceRisk = (entry - stop).abs();
    if (riskPerUnit == null || priceRisk == 0) return 1;
    return riskPerUnit / priceRisk;
  }

  static Direction _direction(String? raw) =>
      raw == 'SHORT' ? Direction.short : Direction.long;

  static Market _market(String instrumentId) =>
      instrumentId.startsWith('CRYPTO') ? Market.crypto : Market.moex;

  static SetupStrategy _strategy(String? raw) => switch (raw) {
        'BREAKOUT_RETEST' => SetupStrategy.breakoutRetest,
        'WYCKOFF_REVERSAL' => SetupStrategy.wyckoffReversal,
        _ => SetupStrategy.trendPullback,
      };

  static PlanOrderType _orderType(String? raw) => switch (raw) {
        'STOP_CONFIRMATION' => PlanOrderType.stopLimit,
        'MARKET' => PlanOrderType.market,
        _ => PlanOrderType.limit,
      };

  /// Шестнадцать состояний §18 движка сводятся к состояниям карточки.
  ///
  /// Сведение — не потеря: у движка есть промежуточные состояния исполнения,
  /// которых на карточке идеи не бывает, и показывать их как отдельные виды
  /// идеи значит смешать замысел с его исполнением.
  static IdeaState _state(String? raw) => switch (raw) {
        'DISCOVERED' || 'WATCH' || 'PENDING' => IdeaState.watch,
        'TRIGGERED' || 'ARMED' => IdeaState.triggered,
        // Исполнительные состояния §18 — это позиция, а не наблюдение.
        //
        // Здесь стояли `PARTIAL` и `SCALED`, которых у движка нет вовсе:
        // цикл называет их `PARTIALLY_FILLED`, `FILLED`, `TP1_HIT`,
        // `MANAGING`, `TP2_HIT`. Все пять падали в `_ => watch`, и владелец
        // видел «Идея всё ещё в наблюдении, хотя точка входа уже случилась»
        // — причём чем дальше сделка ушла по плану, тем увереннее карточка
        // утверждала, что ничего не происходит.
        'ACTIVE' ||
        'PARTIALLY_FILLED' ||
        'FILLED' ||
        'TP1_HIT' ||
        'MANAGING' ||
        'TP2_HIT' =>
          IdeaState.active,
        'CLOSED' || 'STOPPED' || 'TARGET_REACHED' => IdeaState.closed,
        'CANCELLED' || 'REJECTED' => IdeaState.invalidated,
        // Идея, снятая риском или качеством данных, — не наблюдение: ждать
        // от неё нечего, пока не снимут блокировку.
        'RISK_BLOCKED' || 'DATA_BLOCKED' => IdeaState.invalidated,
        // Рынок ушёл без входа — своё состояние, а не «срок истёк».
        // Раньше оба падали в watch, и обогнанная рынком идея неделю
        // выглядела живой: «Watch, сигнал живёт 4 дн 6 ч» под ценой у TP3.
        'MISSED' => IdeaState.missed,
        'EXPIRED' || 'TIMED_OUT' => IdeaState.expired,
        'SKIPPED' => IdeaState.skipped,
        _ => IdeaState.watch,
      };

  static AnnotationType? _annotationType(String? raw) {
    if (raw == null) return null;
    for (final t in AnnotationType.values) {
      if (t.name == raw) return t;
    }
    return null;
  }

  static List<DataQualityFlag> _flags(List<dynamic> raw) {
    final out = <DataQualityFlag>[];
    for (final item in raw) {
      final name = item.toString();
      for (final flag in DataQualityFlag.values) {
        if (flag.name.toUpperCase() == name.toUpperCase() ||
            flag.code == name) {
          out.add(flag);
          break;
        }
      }
    }
    return out;
  }

  /// Разбор ответа портфельного контура (§6).
  ///
  /// Доли приходят строками — как и цены, и по той же причине: из доли
  /// считается сумма покупки, а `0.075` в JSON-числе на другом конце легко
  /// становится `0.07499999999999999`.
  static PortfolioState portfolio(Map<String, dynamic> json) {
    final status = json['status'];
    final statusMap = status is Map<String, dynamic> ? status : const {};
    return PortfolioState(
      stages: [
        for (final item in (statusMap['stages'] as List? ?? const []))
          if (item is Map<String, dynamic>)
            PortfolioStage(
              key: '${item['key'] ?? ''}',
              name: '${item['name'] ?? ''}',
              done: item['done'] == true,
              detail: '${item['detail'] ?? ''}',
            ),
      ],
      packages: [
        for (final item in (json['packages'] as List? ?? const []))
          if (item is Map<String, dynamic>) _package(item),
      ],
      universeMix: [
        for (final item in (statusMap['universe_mix'] as List? ?? const []))
          if (item is Map<String, dynamic>)
            UniverseSlice(
              assetClass: '${item['asset_class'] ?? ''}',
              label: '${item['label'] ?? ''}',
              total: (item['total'] as num?)?.toInt() ?? 0,
              withHistory: (item['with_history'] as num?)?.toInt() ?? 0,
            ),
      ],
      reason: '${statusMap['reason'] ?? ''}',
      universeNotes: [
        for (final note in (statusMap['universe_notes'] as List? ?? const []))
          '$note',
      ],
      jobs: [
        for (final job in (statusMap['jobs'] as List? ?? const [])) '$job',
      ],
      universe: (statusMap['universe'] as num?)?.toInt() ?? 0,
      generatedAt: _time(statusMap['generated_at']),
    );
  }

  static EnginePackage _package(Map<String, dynamic> json) {
    final now = DateTime.now();
    return EnginePackage(
      id: '${json['id'] ?? ''}',
      profile: '${json['profile'] ?? ''}',
      size: PackageSize.parse('${json['package']}') ?? PackageSize.balanced,
      horizonYears: (json['horizon_years'] as num?)?.toInt() ?? 1,
      expectedLow: _num(json['expected_return_low']),
      expectedHigh: _num(json['expected_return_high']),
      volatility: _num(json['target_volatility']),
      drawdown: _num(json['drawdown_limit']),
      cvar95: _numOrNull(json['cvar_95']),
      rationale: '${json['rationale'] ?? ''}',
      meetsTarget: json['meets_target'] != false,
      warnings: [
        for (final w in (json['warnings'] as List? ?? const [])) '$w',
      ],
      stress: {
        for (final entry in (json['stress'] as Map? ?? const {}).entries)
          '${entry.key}': '${entry.value}',
      },
      mix: [
        for (final item in (json['mix'] as List? ?? const []))
          if (item is Map<String, dynamic>)
            ClassSlice(
              assetClass: '${item['asset_class'] ?? ''}',
              label: '${item['label'] ?? ''}',
              weight: _num(item['weight']),
            ),
      ],
      positions: [
        for (final item in (json['positions'] as List? ?? const []))
          if (item is Map<String, dynamic>)
            PackagePosition(
              instrumentId: '${item['instrument_id'] ?? ''}',
              symbol: '${item['symbol'] ?? ''}',
              title: '${item['title'] ?? ''}',
              assetClass: '${item['asset_class'] ?? ''}',
              weight: _num(item['target_weight']),
              role: '${item['role'] ?? ''}',
              thesis: '${item['thesis'] ?? ''}',
              killConditions: '${item['kill_conditions'] ?? ''}',
              score: _numOrNull(item['score']),
              expectedReturn: _numOrNull(item['expected_return']),
            ),
      ],
      generatedAt: _time(json['generated_at']) ?? now,
      validUntil: _time(json['valid_until']) ?? now,
    );
  }

  /// Бумажные сделки движка (§18, §21).
  ///
  /// Цены приходят строками намеренно (`Money` в схемах сервера): JSON-число
  /// это double, и шаг цены 0,005 на другом конце становится
  /// 0,004999999999999999.
  ///
  /// Сделка без входа или без стопа не разбирается вовсе. Дорисованный нулём
  /// стоп на карточке выглядит как «защиты нет», и это утверждение, которого
  /// никто не делал.
  static List<PaperPosition> paperTrades(List<dynamic> raw) {
    final out = <PaperPosition>[];
    for (final item in raw) {
      if (item is! Map<String, dynamic>) continue;
      final entry = _numOrNull(item['entry']);
      final initial = _numOrNull(item['initial_stop']);
      if (entry == null || initial == null) continue;
      final status = PaperPositionStatus.parse('${item['status']}');
      out.add(PaperPosition(
        id: '${item['id']}',
        ideaId: '${item['idea_id'] ?? ''}',
        instrumentId: '${item['instrument_id'] ?? ''}',
        symbol: '${item['symbol'] ?? item['instrument_id'] ?? ''}',
        long: '${item['direction']}'.toUpperCase() == 'LONG',
        pending: status == PaperPositionStatus.pending,
        status: status,
        entry: entry,
        initialStop: initial,
        // Текущий стоп отсутствовать не должен, но если сервер старой версии
        // его не прислал — исходный честнее нуля.
        currentStop: _numOrNull(item['current_stop']) ?? initial,
        tpPrices: [
          for (final p in (item['tp_prices'] as List<dynamic>? ?? const []))
            ?_numOrNull(p),
        ],
        tpsTaken: (item['tps_taken'] as num?)?.toInt() ?? 0,
        breakevenAt: _time(item['breakeven_at']),
        atBreakeven: item['at_breakeven'] == true,
        // Зафиксированное, а не плавающее: сервер суммирует взятые цели и
        // открытый остаток в это число не входит.
        resultR: _numOrNull(item['realized_r']),
        resultRealized: true,
        lastReconciledAt: _time(item['last_reconciled_at']),
        staleHours: (item['stale_hours'] as num?)?.toInt(),
        closedAt: _time(item['closed_at']),
        outcome: '${item['outcome'] ?? ''}',
        closeReason: '${item['close_reason'] ?? ''}',
        fromServer: true,
      ));
    }
    return out;
  }

  /// Одна paper-сделка из ответа решения.
  ///
  /// Решение обязано оборачивать сделку в `{trade: ...}` и отдельно явно
  /// подтверждать `paper_only: true`. Bare-объект не разбирается: его режим
  /// исполнения неизвестен, а считать неизвестное paper было бы fail-open.
  static PaperPosition? paperTrade(Object? raw) {
    if (raw is! Map<String, dynamic>) return null;
    final nested = raw['trade'];
    if (nested is! Map<String, dynamic>) return null;
    final parsed = paperTrades([nested]);
    return parsed.isEmpty ? null : parsed.single;
  }

  static DateTime? _time(Object? raw) =>
      raw is String ? DateTime.tryParse(raw)?.toLocal() : null;

  static double _num(Object? v) => _numOrNull(v) ?? 0;

  static Map<String, String> _stringMap(Object? raw) => raw is Map
      ? {
          for (final entry in raw.entries) '${entry.key}': '${entry.value}',
        }
      : const {};

  static List<String> _strings(Object? raw) => raw is List
      ? [for (final value in raw) '$value']
      : const [];

  static double? _numOrNull(Object? v) => switch (v) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };
}
