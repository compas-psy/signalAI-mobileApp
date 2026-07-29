import '../enums.dart';

/// Фактор интеграционного анализа: «почему это сделка».
///
/// [weight] 1–3 — вклад фактора, рисуется полоской в карточке идеи.
class SignalFactor {
  const SignalFactor({
    required this.name,
    required this.text,
    required this.weight,
    double? fraction,
  }) : _fraction = fraction;

  final String name;
  final String text;
  final int weight;

  /// Точная доля выполнения фактора 0…1.
  ///
  /// [weight] огрубляет её до трёх ступеней ради полоски в карточке. Скоринг
  /// ТЗ §14.2 работает с долей, а не со ступенью: 0,74 и 0,41 не одно и то же,
  /// хотя обе дают вес 2.
  final double? _fraction;

  /// Доля выполнения. Если точное значение не сохранилось (старый кэш),
  /// восстанавливается по ступени — с потерей точности, но без вранья.
  double get fraction => _fraction ?? (weight / 3);

  /// Заполнение полоски, 0–100%.
  double get fillPercent => fraction * 100;

  factory SignalFactor.fromJson(Map<String, dynamic> j) => SignalFactor(
        name: j['name'] as String,
        text: j['text'] as String,
        weight: (j['weight'] as num).toInt(),
        fraction: (j['fraction'] as num?)?.toDouble(),
      );

  Map<String, dynamic> toJson() => {
        'name': name,
        'text': text,
        'weight': weight,
        if (_fraction != null) 'fraction': _fraction,
      };
}

/// Уровень фиксации прибыли с долей объёма.
class TakeProfit {
  const TakeProfit({
    required this.index,
    required this.price,
    required this.sharePercent,
  });

  /// Номер уровня, начиная с 1.
  final int index;
  final double price;

  /// Доля объёма, закрываемая на этом уровне (ТЗ §6.5 — из конфига стратегии).
  final int sharePercent;

  String get label => 'TP$index';

  factory TakeProfit.fromJson(Map<String, dynamic> j) => TakeProfit(
        index: (j['index'] as num).toInt(),
        price: (j['price'] as num).toDouble(),
        sharePercent: (j['share_percent'] as num).toInt(),
      );

  Map<String, dynamic> toJson() =>
      {'index': index, 'price': price, 'share_percent': sharePercent};
}

/// Событие календаря/новостей, привязанное к идее (ТЗ §3, §5.4).
class MarketEvent {
  const MarketEvent({
    required this.time,
    required this.text,
    required this.impact,
    this.affects,
  });

  /// Отображаемое время («16:30», «28.07», «пн»).
  final String time;
  final String text;
  final EventImpact impact;

  /// Инструменты, на которые влияет событие («MXU6 · Si»).
  final String? affects;

  factory MarketEvent.fromJson(Map<String, dynamic> j) => MarketEvent(
        time: j['time'] as String,
        text: j['text'] as String,
        impact: EventImpact.parse(j['impact'] as String? ?? 'low'),
        affects: j['affects'] as String?,
      );

  Map<String, dynamic> toJson() =>
      {'time': time, 'text': text, 'impact': impact.name, 'affects': affects};
}

/// Свеча графика идеи: только OHLC.
///
/// Сознательно без времени, объёма и OI — график идеи рисует цену и уровни,
/// а держать в памяти лишние поля на каждую свечу каждого сигнала незачем.
class ChartCandle {
  const ChartCandle(this.open, this.high, this.low, this.close);

  final double open;
  final double high;
  final double low;
  final double close;

  factory ChartCandle.fromJson(List<dynamic> j) => ChartCandle(
        (j[0] as num).toDouble(),
        (j[1] as num).toDouble(),
        (j[2] as num).toDouble(),
        (j[3] as num).toDouble(),
      );

  List<double> toJson() => [open, high, low, close];
}

/// Ценовая зона на графике (например, незакрытый FVG).
class ChartZone {
  const ChartZone({
    required this.from,
    required this.to,
    required this.startIndex,
    required this.label,
  });

  final double from;
  final double to;

  /// Свеча, с которой зона начинается (индекс в [SignalChart.candles]).
  final int startIndex;
  final String label;

  factory ChartZone.fromJson(Map<String, dynamic> j) => ChartZone(
        from: (j['from'] as num).toDouble(),
        to: (j['to'] as num).toDouble(),
        startIndex: (j['start_index'] as num?)?.toInt() ?? 0,
        label: j['label'] as String? ?? '',
      );

  Map<String, dynamic> toJson() =>
      {'from': from, 'to': to, 'start_index': startIndex, 'label': label};
}

/// Данные для графика идеи: реальные свечи и реальная разметка.
///
/// Здесь нет ничего нарисованного «для красоты»: свечи — те самые, по которым
/// скринер считал сигнал (усечённое окно, чтобы не держать в памяти полную
/// историю), зоны и уровень слома — только если скринер их действительно нашёл.
class SignalChart {
  const SignalChart({
    required this.timeframeLabel,
    required this.candles,
    this.breakLevel,
    this.breakLabel,
    this.zones = const [],
  });

  /// Таймфрейм отображаемых свечей — всегда виден тегом на графике («1H»).
  final String timeframeLabel;

  final List<ChartCandle> candles;

  /// Уровень слома структуры; null — слома не было, линия не рисуется.
  final double? breakLevel;

  /// «BOS» / «CHoCH» — подпись слома, если он есть.
  final String? breakLabel;

  /// Реально найденные зоны (FVG и т.п.); пусто — не рисуем.
  final List<ChartZone> zones;

  factory SignalChart.fromJson(Map<String, dynamic> j) => SignalChart(
        timeframeLabel: j['timeframe'] as String? ?? '',
        candles: (j['candles'] as List<dynamic>? ?? const [])
            .map((e) => ChartCandle.fromJson(e as List<dynamic>))
            .toList(growable: false),
        breakLevel: (j['break_level'] as num?)?.toDouble(),
        breakLabel: j['break_label'] as String?,
        zones: (j['zones'] as List<dynamic>? ?? const [])
            .map((e) => ChartZone.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
      );

  Map<String, dynamic> toJson() => {
        'timeframe': timeframeLabel,
        'candles': [for (final c in candles) c.toJson()],
        'break_level': breakLevel,
        'break_label': breakLabel,
        'zones': [for (final z in zones) z.toJson()],
      };
}

/// Торговый сигнал (идея) — центральная сущность приложения.
///
/// Все уровни и скоринг приходят с сервера (ТЗ §5): клиент ничего не считает,
/// кроме форматирования и отображения.
class TradingSignal {
  const TradingSignal({
    required this.id,
    required this.symbol,
    required this.name,
    required this.market,
    required this.direction,
    required this.horizon,
    required this.horizonLabel,
    required this.score,
    required this.entry,
    required this.stopLoss,
    required this.takeProfits,
    required this.priceDecimals,
    required this.riskReward,
    required this.chips,
    required this.note,
    required this.factors,
    required this.events,
    required this.unitRisk,
    required this.unitRiskLabel,
    required this.unitMultiplier,
    required this.unitDecimals,
    required this.unitName,
    required this.lastPrice,
    required this.changeLabel,
    required this.changeUp,
    required this.status,
    this.validUntil,
    this.invalidationPrice,
    this.correlationGroup,
    this.strategyId,
    this.chart,
    this.entryIsStop = false,
  });

  final String id;

  /// Тикер: SiU6, BR-9.26, BTCUSDT…
  final String symbol;

  /// Человекочитаемое имя инструмента.
  final String name;
  final Market market;
  final Direction direction;
  final Horizon horizon;

  /// Подпись горизонта в карточке («1–3 дня»).
  final String horizonLabel;

  /// SignalScore 0–100 (ТЗ §5.3).
  final int score;

  final double entry;
  final double stopLoss;
  final List<TakeProfit> takeProfits;

  /// Число знаков после запятой для цен инструмента.
  final int priceDecimals;

  /// R:R до TP2 в виде готовой подписи («2,3»).
  final String riskReward;

  /// Короткие теги сетапа: Spring, OB 1H, RSI-див…
  final List<String> chips;

  /// Обоснование в 2–3 предложения (ТЗ §5.2).
  final String note;

  final List<SignalFactor> factors;
  final List<MarketEvent> events;

  /// Риск в рублях на одну единицу инструмента (контракт / лот / шаг крипты).
  final double unitRisk;

  /// Подпись под объёмом: «550 ₽ / контракт».
  final String unitRiskLabel;

  /// Множитель единицы для крипты (0.01 BTC и т.п.).
  final double unitMultiplier;

  /// Знаков после запятой в объёме позиции.
  final int unitDecimals;

  /// Единица измерения объёма: «конт.», «лот.», «BTC».
  final String unitName;

  /// Последняя цена, уже отформатированная сервером.
  final String lastPrice;

  /// Изменение за день, готовая подпись («+0,31%»).
  final String changeLabel;
  final bool changeUp;

  final SignalStatus status;

  /// Время жизни сигнала (ТЗ §1, §5.4).
  final DateTime? validUntil;

  /// Цена инвалидации идеи.
  final double? invalidationPrice;

  /// Группа корреляции — «сделки не толкаются плечами» (ТЗ §1, §6.3).
  final String? correlationGroup;

  final String? strategyId;

  /// Реальные свечи и разметка для графика. null — графика нет (и рисовать
  /// вместо него муляж нельзя: виджет покажет честную заглушку).
  final SignalChart? chart;

  /// Вход стоп-заявкой по пробою, а не лимиткой на откате. Решение скринера:
  /// от него зависит и сторона исполнения в движке, и тип заявки на бирже.
  final bool entryIsStop;

  /// Расстояние от входа до стопа в единицах цены — 1R.
  ///
  /// Не путать с [unitRisk]: тот же риск, но уже пересчитанный в рубли через
  /// стоимость пункта (`InstrumentSpec.riskPerUnit`). Здесь — только цена.
  double get priceRisk => (entry - stopLoss).abs();

  TradingSignal copyWith({SignalStatus? status}) => TradingSignal(
        id: id,
        symbol: symbol,
        name: name,
        market: market,
        direction: direction,
        horizon: horizon,
        horizonLabel: horizonLabel,
        score: score,
        entry: entry,
        stopLoss: stopLoss,
        takeProfits: takeProfits,
        priceDecimals: priceDecimals,
        riskReward: riskReward,
        chips: chips,
        note: note,
        factors: factors,
        events: events,
        unitRisk: unitRisk,
        unitRiskLabel: unitRiskLabel,
        unitMultiplier: unitMultiplier,
        unitDecimals: unitDecimals,
        unitName: unitName,
        lastPrice: lastPrice,
        changeLabel: changeLabel,
        changeUp: changeUp,
        status: status ?? this.status,
        validUntil: validUntil,
        invalidationPrice: invalidationPrice,
        correlationGroup: correlationGroup,
        strategyId: strategyId,
        chart: chart,
      );

  factory TradingSignal.fromJson(Map<String, dynamic> j) => TradingSignal(
        id: j['id'] as String,
        symbol: j['symbol'] as String,
        name: j['name'] as String,
        market: Market.parse(j['market'] as String),
        direction: Direction.parse(j['direction'] as String),
        horizon: Horizon.parse(j['horizon'] as String? ?? 'swing'),
        horizonLabel: j['horizon_label'] as String? ?? '',
        score: (j['score'] as num).toInt(),
        entry: (j['entry'] as num).toDouble(),
        stopLoss: (j['stop_loss'] as num).toDouble(),
        takeProfits: (j['take_profits'] as List<dynamic>)
            .map((e) => TakeProfit.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        priceDecimals: (j['price_decimals'] as num?)?.toInt() ?? 0,
        riskReward: j['risk_reward'] as String? ?? '',
        chips: (j['chips'] as List<dynamic>? ?? const [])
            .map((e) => e as String)
            .toList(growable: false),
        note: j['note'] as String? ?? '',
        factors: (j['factors'] as List<dynamic>? ?? const [])
            .map((e) => SignalFactor.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        events: (j['events'] as List<dynamic>? ?? const [])
            .map((e) => MarketEvent.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        unitRisk: (j['unit_risk'] as num).toDouble(),
        unitRiskLabel: j['unit_risk_label'] as String? ?? '',
        unitMultiplier: (j['unit_multiplier'] as num?)?.toDouble() ?? 1,
        unitDecimals: (j['unit_decimals'] as num?)?.toInt() ?? 0,
        unitName: j['unit_name'] as String? ?? '',
        lastPrice: j['last_price'] as String? ?? '',
        changeLabel: j['change_label'] as String? ?? '',
        changeUp: j['change_up'] as bool? ?? true,
        status: SignalStatus.parse(j['status'] as String? ?? 'proposed'),
        validUntil: j['valid_until'] == null
            ? null
            : DateTime.tryParse(j['valid_until'] as String)?.toLocal(),
        invalidationPrice: (j['invalidation_price'] as num?)?.toDouble(),
        correlationGroup: j['correlation_group'] as String?,
        strategyId: j['strategy_id'] as String?,
        entryIsStop: j['entry_is_stop'] as bool? ?? false,
        chart: j['chart'] == null
            ? null
            : SignalChart.fromJson(j['chart'] as Map<String, dynamic>),
      );

  Map<String, dynamic> toJson() => {
        'id': id,
        'symbol': symbol,
        'name': name,
        'market': market.label,
        'direction': direction.name,
        'horizon': horizon.name,
        'horizon_label': horizonLabel,
        'score': score,
        'entry': entry,
        'stop_loss': stopLoss,
        'take_profits': [for (final tp in takeProfits) tp.toJson()],
        'price_decimals': priceDecimals,
        'risk_reward': riskReward,
        'chips': chips,
        'note': note,
        'factors': [for (final f in factors) f.toJson()],
        'events': [for (final e in events) e.toJson()],
        'unit_risk': unitRisk,
        'unit_risk_label': unitRiskLabel,
        'unit_multiplier': unitMultiplier,
        'unit_decimals': unitDecimals,
        'unit_name': unitName,
        'last_price': lastPrice,
        'change_label': changeLabel,
        'change_up': changeUp,
        'status': status.name,
        'valid_until': validUntil?.toIso8601String(),
        'invalidation_price': invalidationPrice,
        'correlation_group': correlationGroup,
        'strategy_id': strategyId,
        'chart': chart?.toJson(),
        'entry_is_stop': entryIsStop,
      };
}
