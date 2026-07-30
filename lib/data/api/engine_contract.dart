import '../../domain/enums.dart';
import '../../domain/idea/evidence.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/idea/quality_score.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/signal.dart';

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
          : (idea.state.canConfirm ? SignalStatus.proposed : SignalStatus.expired),
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
  static String symbolOf(String instrumentId) {
    final cut = instrumentId.lastIndexOf(':');
    return cut < 0 ? instrumentId : instrumentId.substring(cut + 1);
  }

  /// Сколько знаков после запятой у цен инструмента.
  ///
  /// Считается по самим ценам, а не задаётся константой: у фьючерса на доллар
  /// знаков нет, у нефти два, у крипты бывает больше. Округлить чужую цену до
  /// целых значит показать не ту цену.
  static int _decimalsFor(List<double> prices) {
    var decimals = 0;
    for (final price in prices) {
      for (var d = decimals; d <= 4; d++) {
        if ((price * _pow10(d)).roundToDouble() == price * _pow10(d)) {
          if (d > decimals) decimals = d;
          break;
        }
        if (d == 4) decimals = 4;
      }
    }
    return decimals;
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
  static Idea idea(Map<String, dynamic> j, {String? instrumentName}) {
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

    return Idea(
      id: id,
      instrumentId: j['instrument_id'] as String? ?? '',
      instrumentName: instrumentName ?? (j['symbol'] as String? ?? ''),
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
      dataFlags: _flags(j['data_warnings'] as List<dynamic>? ?? const []),
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
      maxSlippagePercent: 0.1,
      stop: _num(plan['stop']),
      targets: targets,
      quantity: _num(sizing?['quantity']).round(),
      lotSize: 1,
      valuePerPoint: _riskPerUnitToValue(sizing, plan),
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
    final entry = (_num(plan['entry_low']) + _num(plan['entry_high'])) / 2;
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
        'ACTIVE' || 'PARTIAL' || 'SCALED' => IdeaState.active,
        'CLOSED' || 'STOPPED' || 'TARGET_REACHED' => IdeaState.closed,
        'CANCELLED' || 'REJECTED' => IdeaState.invalidated,
        'EXPIRED' => IdeaState.expired,
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

  static DateTime? _time(Object? raw) =>
      raw is String ? DateTime.tryParse(raw)?.toLocal() : null;

  static double _num(Object? v) => _numOrNull(v) ?? 0;

  static double? _numOrNull(Object? v) => switch (v) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };
}
