import 'dart:math' as math;

import '../enums.dart';
import '../models/signal.dart';
import 'candle.dart';
import 'factor_audit.dart';
import 'indicators.dart';
import 'instrument_spec.dart';

/// Режим рынка: куда смотрят индекс, валюта и сырьё (ТЗ §5.1).
class MarketRegime {
  const MarketRegime({
    required this.indexTrend,
    required this.currencyTrend,
    required this.cryptoTrend,
  });

  final StructureTrend indexTrend;
  final StructureTrend currencyTrend;
  final StructureTrend cryptoTrend;

  static const unknown = MarketRegime(
    indexTrend: StructureTrend.flat,
    currencyTrend: StructureTrend.flat,
    cryptoTrend: StructureTrend.flat,
  );

  /// Тренд, релевантный для рынка инструмента.
  StructureTrend forMarket(Market market) => switch (market) {
        Market.crypto => cryptoTrend,
        Market.moex => indexTrend,
        Market.forts => indexTrend,
      };
}

/// Режим рынка на любой момент прошлого — по дневной структуре якорей.
///
/// Нужен бэктесту: блок «Режим рынка» весит заметную долю оценки и вживую
/// вырезает контр-трендовые идеи. Прогон с постоянным [MarketRegime.unknown]
/// торговал бы стратегию, которой не существует, и оптимизатор подбирал бы
/// параметры под неё же.
///
/// Тренды считаются заранее, по одному значению на закрывшуюся дневку: звать
/// [analyzeStructure] на каждом часовом баре прогона (тысячи баров × три
/// якоря) было бы неприемлемо дорого.
class RegimeTimeline {
  RegimeTimeline._(this._index, this._currency, this._crypto);

  final List<(DateTime, StructureTrend)> _index;
  final List<(DateTime, StructureTrend)> _currency;
  final List<(DateTime, StructureTrend)> _crypto;

  /// Собирает шкалу из дневок якорей: индекс, валюта, биткоин.
  factory RegimeTimeline.build({
    List<Candle> index = const [],
    List<Candle> currency = const [],
    List<Candle> crypto = const [],
  }) =>
      RegimeTimeline._(_series(index), _series(currency), _series(crypto));

  /// Пустая шкала: режим всегда нейтральный. Явный объект вместо null —
  /// чтобы «якорей не было» читалось в коде, а не угадывалось.
  static final empty = RegimeTimeline.build();

  /// Сколько дневок нужно, чтобы структура вообще что-то значила.
  static const _minDaily = 20;

  static List<(DateTime, StructureTrend)> _series(List<Candle> daily) {
    if (daily.length < _minDaily) return const [];
    return [
      for (var i = _minDaily; i <= daily.length; i++)
        // Метка — закрытие последней вошедшей свечи: раньше него этот тренд
        // ещё не был известен, и заглядывания в будущее нет.
        (daily[i - 1].time, analyzeStructure(daily.sublist(0, i)).trend),
    ];
  }

  /// Режим на момент [moment] — по якорям, закрывшимся строго до него.
  MarketRegime at(DateTime moment) => MarketRegime(
        indexTrend: _trendAt(_index, moment),
        currencyTrend: _trendAt(_currency, moment),
        cryptoTrend: _trendAt(_crypto, moment),
      );

  static StructureTrend _trendAt(
    List<(DateTime, StructureTrend)> series,
    DateTime moment,
  ) {
    if (series.isEmpty || !series.first.$1.isBefore(moment)) {
      return StructureTrend.flat;
    }
    // Двоичный поиск последней метки строго раньше момента.
    var low = 0;
    var high = series.length - 1;
    while (low < high) {
      final mid = (low + high + 1) ~/ 2;
      if (series[mid].$1.isBefore(moment)) {
        low = mid;
      } else {
        high = mid - 1;
      }
    }
    return series[low].$2;
  }
}

/// Группа коррелированных инструментов: в одну группу — не больше одной идеи.
///
/// Живёт на верхнем уровне, потому что нужна и скринеру (дедупликация выдачи),
/// и риск-движку (лимит одновременных позиций). Две копии таблицы групп
/// неизбежно разъехались бы.
String correlationGroup(InstrumentSpec spec) =>
    correlationGroupFor(spec.symbol, spec.market);

/// То же по символу и рынку — журналу сделок спецификации инструмента
/// недоступны, а группу знать надо.
String correlationGroupFor(String rawSymbol, Market market) {
  final symbol = rawSymbol.toUpperCase();
  if (symbol.startsWith('SI') ||
      symbol.startsWith('CNY') ||
      symbol.startsWith('CR') ||
      symbol.startsWith('ED')) {
    return 'currency';
  }
  if (symbol.startsWith('BR') || symbol.startsWith('NG')) return 'energy';
  // Металлы: длинные корни и короткие коды серий (GOLD→GD, SILV→SV).
  if (symbol.startsWith('GOLD') ||
      symbol.startsWith('GD') ||
      symbol.startsWith('SILV') ||
      symbol.startsWith('SV')) {
    return 'metals';
  }
  if (market == Market.crypto) return 'crypto';
  if (market == Market.moex) return 'stocks';
  return 'index';
}

/// Входные данные скринера по одному инструменту.
class InstrumentInput {
  const InstrumentInput({
    required this.spec,
    required this.hourly,
    required this.daily,
    required this.lastPrice,
    required this.changePercentLabel,
    required this.changeUp,
    this.openInterestChangePercent,
    this.fundingRate,
  });

  final InstrumentSpec spec;

  /// Часовые свечи — рабочий таймфрейм структуры и входа.
  final List<Candle> hourly;

  /// Дневные свечи — контекст и ATR для целей.
  final List<Candle> daily;

  final double lastPrice;

  /// Готовая подпись изменения за день: «+0,31%».
  final String changePercentLabel;
  final bool changeUp;

  /// Изменение открытого интереса за сутки, %.
  final double? openInterestChangePercent;

  /// Ставка финансирования (только крипта), доля: −0.00008 = −0,008%.
  final double? fundingRate;
}

/// Вклад одного блока в SignalScore.
class ScoreComponent {
  const ScoreComponent({
    required this.name,
    required this.points,
    required this.maxPoints,
    required this.note,
  });

  final String name;
  final double points;
  final double maxPoints;
  final String note;

  /// Вес 1–3 для полоски фактора в карточке идеи.
  int get weight {
    final share = maxPoints == 0 ? 0 : points / maxPoints;
    if (share >= 0.75) return 3;
    if (share >= 0.4) return 2;
    return 1;
  }
}

/// Причина, по которой кандидат отброшен — попадает в лог, а не молча теряется.
///
/// [price] — цена инструмента на момент отбраковки: по ней журнал сигналов
/// потом честно меряет, что было бы, если бы фильтр не сработал.
class RejectedCandidate {
  const RejectedCandidate(this.symbol, this.reason, {this.price});

  final String symbol;
  final String reason;
  final double? price;
}

/// Результат работы скринера по инструменту.
class ScreenerResult {
  const ScreenerResult({required this.signal, required this.components});

  final TradingSignal signal;
  final List<ScoreComponent> components;
}

/// Способ входа в сделку.
enum EntryType {
  /// Лимитка на ретесте уровня: вход на откате.
  limit,

  /// Стоп-заявка за пробойным баром: вход по подтверждённой силе.
  stopBreak;

  static EntryType parse(String? v) =>
      v == 'stop_break' ? EntryType.stopBreak : EntryType.limit;

  String get id => this == EntryType.stopBreak ? 'stop_break' : 'limit';
}

/// Свести часовые свечи к старшему таймфрейму (кратному часу).
///
/// Живёт рядом со скринером: это его представление о структуре 4H, и второй
/// потребитель у ресемпла не планируется. Границы — по кратности часа эпохи,
/// как их рисует и биржа.
List<Candle> resampleHours(List<Candle> hourly, int hours) {
  if (hourly.isEmpty || hours <= 1) return hourly;
  final result = <Candle>[];
  Candle? current;
  DateTime? bucket;
  for (final c in hourly) {
    final b = DateTime.utc(c.time.year, c.time.month, c.time.day, c.time.hour ~/ hours * hours);
    if (bucket != b) {
      if (current != null) result.add(current);
      bucket = b;
      current = Candle(
        time: b,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      );
    } else {
      current = Candle(
        time: b,
        open: current!.open,
        high: c.high > current.high ? c.high : current.high,
        low: c.low < current.low ? c.low : current.low,
        close: c.close,
        volume: current.volume + c.volume,
      );
    }
  }
  if (current != null) result.add(current);
  return result;
}

/// Свести дневные свечи к неделькам — контекст для среднесрочного скринера.
///
/// Границы — по понедельникам ISO-недели: так недельки рисует и биржа.
List<Candle> resampleWeeks(List<Candle> daily) {
  if (daily.isEmpty) return daily;
  DateTime weekStart(DateTime t) {
    final d = DateTime.utc(t.year, t.month, t.day);
    return d.subtract(Duration(days: d.weekday - 1));
  }

  final result = <Candle>[];
  Candle? current;
  DateTime? bucket;
  for (final c in daily) {
    final b = weekStart(c.time);
    if (bucket != b) {
      if (current != null) result.add(current);
      bucket = b;
      current = Candle(
        time: b,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume,
      );
    } else {
      current = Candle(
        time: b,
        open: current!.open,
        high: c.high > current.high ? c.high : current.high,
        low: c.low < current.low ? c.low : current.low,
        close: c.close,
        volume: current.volume + c.volume,
      );
    }
  }
  if (current != null) result.add(current);
  return result;
}

/// Детерминированный скринер первой ступени (ТЗ §5.1 и §5.3).
///
/// Считает всё на устройстве по публичным данным: LLM-ступени здесь нет,
/// поэтому её вес исключается из знаменателя, а не обнуляет оценку.
class Screener {
  const Screener({
    this.minScore = 60,
    this.minRiskRewardToTp2 = 1.5,
    this.maxEntryDistanceAtr = 0.5,
    this.minDaysToExpiration = 3,
    this.takeProfitMultiples = defaultTakeProfitMultiples,
    this.takeProfitShares = defaultTakeProfitShares,
    this.requireTrigger = false,
    this.entryType = EntryType.limit,
    this.align4h = false,
    this.timeframeLabel = '1H',
  });

  /// Ниже этого SignalScore кандидат не показывается вовсе.
  final int minScore;

  /// Минимальное соотношение риск/прибыль до второго тейка (ТЗ §5.2).
  final double minRiskRewardToTp2;

  /// Насколько далеко от текущей цены может быть вход, в ATR(1H).
  final double maxEntryDistanceAtr;

  /// Фильтр близкой экспирации (ТЗ §5.4).
  final int minDaysToExpiration;

  /// Требовать триггерную свечу (пин-бар, поглощение или внутренний бар в
  /// сторону сделки) на последнем баре — без неё кандидат отбрасывается.
  ///
  /// Классический фильтр качества ретеста: лимитка в зоне без реакции цены
  /// на уровень чаще прошивается насквозь. Включается walk-forward подбором,
  /// а не руками: цена фильтра — меньше сделок, и платить её стоит только
  /// там, где out-of-sample данные показывают выигрыш.
  final bool requireTrigger;

  /// Способ входа: лимитка на ретесте или стоп-заявка за пробойным баром.
  final EntryType entryType;

  /// Требовать, чтобы структура 4H не противоречила направлению сделки.
  ///
  /// Мультитаймфреймовое согласование: 1H даёт вход, 4H — контекст. Идея
  /// лонга при падающей 4H-структуре — торговля против ближайшего старшего
  /// потока; флэт 4H проходит.
  final bool align4h;

  /// Подпись рабочего таймфрейма на графике идеи. Скринер сам не знает,
  /// какие бары ему передали, — знает вызывающий: у свинга это 1H, у акций 1D.
  final String timeframeLabel;

  /// Доли фиксации по тейкам. Настраиваемы: walk-forward оптимизация может
  /// выбрать другой профиль фиксации.
  final List<int> takeProfitShares;

  /// Кратности риска для тейков.
  final List<double> takeProfitMultiples;

  /// Дефолтные доли фиксации (ТЗ §6.5, выбор владельца — 50/30/20).
  static const defaultTakeProfitShares = [50, 30, 20];

  /// Дефолтные кратности тейков.
  static const defaultTakeProfitMultiples = [1.4, 2.2, 3.5];

  /// Веса блоков SignalScore (ТЗ §5.3). Блок LLM (15) в автономном режиме
  /// отсутствует, поэтому итог нормируется на сумму доступных весов.
  static const weightStructure = 20.0;
  static const weightLevel = 15.0;
  static const weightPriceAction = 10.0;
  static const weightRsi = 10.0;
  static const weightVolume = 15.0;
  static const weightRegime = 15.0;

  static double get maxPoints =>
      weightStructure + weightLevel + weightPriceAction + weightRsi + weightVolume + weightRegime;

  /// Прогон по инструменту. Возвращает null, если кандидата нет.
  ///
  /// [factorHistory] — аудит факторов последнего бэктеста: измеренный вклад
  /// каждого блока в R. Он дописывается в обоснование, чтобы владелец
  /// подтверждал сделку, зная, сколько такой сетап приносил на самом деле,
  /// а не только как он называется.
  ScreenerResult? evaluate(
    InstrumentInput input,
    MarketRegime regime, {
    DateTime? now,
    List<RejectedCandidate>? rejected,
    List<FactorEdge>? factorHistory,
  }) {
    final spec = input.spec;
    void reject(String reason) =>
        rejected?.add(RejectedCandidate(spec.symbol, reason, price: input.lastPrice));

    if (input.hourly.length < 60 || input.daily.length < 30) {
      reject('мало истории');
      return null;
    }

    final expiration = spec.expiration;
    if (expiration != null) {
      final days = expiration.difference(now ?? DateTime.now()).inDays;
      if (days < minDaysToExpiration) {
        reject('до экспирации $days дн.');
        return null;
      }
    }

    final hourly = input.hourly;
    final structure = analyzeStructure(hourly);
    final atrHourly = atr(hourly).lastWhere((v) => v != null, orElse: () => null);
    final atrDaily = atr(input.daily).lastWhere((v) => v != null, orElse: () => null);
    if (atrHourly == null || atrHourly <= 0 || atrDaily == null) {
      reject('нет ATR');
      return null;
    }

    final direction = _direction(structure, input.daily);
    if (direction == null) {
      reject('нет направления: структура во флэте');
      return null;
    }
    final long = direction == Direction.long;

    // Вето против режима рынка — жёсткое, а не штраф баллами. Раньше
    // контртрендовая идея могла набрать порог на других блоках, и именно
    // такие сделки при винрейте ~35% тянули профит-фактор ниже единицы:
    // свинг против старшего тренда — ставка на разворот, а мы его не торгуем.
    // Нейтральный режим проходит: в нём у структуры 1H есть право голоса.
    final regimeTrend = regime.forMarket(spec.market);
    if (regimeTrend == StructureTrend.up && !long) {
      reject('шорт против растущего рынка');
      return null;
    }
    if (regimeTrend == StructureTrend.down && long) {
      reject('лонг против падающего рынка');
      return null;
    }

    // Согласование с 4H: вход 1H не должен идти против структуры ближайшего
    // старшего таймфрейма. Флэт проходит — вето только на прямое противоречие.
    if (align4h) {
      final fourHourly = resampleHours(hourly, 4);
      if (fourHourly.length >= 30) {
        final trend4h = analyzeStructure(fourHourly).trend;
        if (trend4h == (long ? StructureTrend.down : StructureTrend.up)) {
          reject('структура 4H против направления');
          return null;
        }
      }
    }

    // Вход зависит от типа заявки. Лимитка — ретест сломанного уровня (или
    // ближайшего экстремума, если слома не было). Стоп-вход — пробой
    // последнего бара: сделка открывается только продолжением движения.
    final double? entry;
    if (entryType == EntryType.stopBreak) {
      final last = hourly.last;
      entry = long ? last.high + spec.tick : last.low - spec.tick;
    } else {
      entry = structure.breakLevel ??
          (long ? structure.lastSwingHigh?.price : structure.lastSwingLow?.price);
    }
    if (entry == null) {
      reject('нет уровня входа');
      return null;
    }
    if ((entry - input.lastPrice).abs() > maxEntryDistanceAtr * atrHourly) {
      reject('вход дальше $maxEntryDistanceAtr·ATR от цены');
      return null;
    }

    // Стоп — за противоположный экстремум с буфером в половину ATR.
    // У стоп-входа экстремум свой: база пробоя (последние три бара), а не
    // дальний свинг. Вход по силе со стопом за всей структурой раздувал бы
    // риск в разы и убивал R:R — классика ставит стоп под пробойную базу.
    final double? opposite;
    if (entryType == EntryType.stopBreak) {
      final base = hourly.sublist(hourly.length - 3);
      opposite = long
          ? base.map((c) => c.low).reduce(math.min)
          : base.map((c) => c.high).reduce(math.max);
    } else {
      opposite = long ? structure.lastSwingLow?.price : structure.lastSwingHigh?.price;
    }
    if (opposite == null) {
      reject('нет уровня для стопа');
      return null;
    }
    final stopLoss = long ? opposite - atrHourly * 0.5 : opposite + atrHourly * 0.5;
    final risk = (entry - stopLoss).abs();
    if (risk < atrHourly * 0.3 || risk > atrHourly * 3) {
      reject('нереалистичный стоп');
      return null;
    }

    final takeProfits = [
      for (var i = 0; i < takeProfitMultiples.length; i++)
        TakeProfit(
          index: i + 1,
          price: long
              ? entry + risk * takeProfitMultiples[i]
              : entry - risk * takeProfitMultiples[i],
          sharePercent: takeProfitShares[i],
        ),
    ];
    final rrToTp2 = (takeProfits[1].price - entry).abs() / risk;
    if (rrToTp2 < minRiskRewardToTp2) {
      reject('R:R до TP2 ниже $minRiskRewardToTp2');
      return null;
    }

    if (requireTrigger && !_hasTrigger(hourly, long)) {
      reject('нет триггерной свечи у уровня');
      return null;
    }

    final components = _withHistory(
      _score(input, structure, regime, direction, entry, atrHourly),
      factorHistory,
    );
    final points = components.fold<double>(0, (sum, c) => sum + c.points);
    final score = (points / maxPoints * 100).round();
    if (score < minScore) {
      reject('score $score ниже порога $minScore');
      return null;
    }

    final unitRisk = spec.riskPerUnit(risk);
    if (unitRisk <= 0) {
      reject('нет стоимости пункта');
      return null;
    }

    final chart = _chart(hourly, structure, direction, entry, atrHourly);

    return ScreenerResult(
      components: components,
      signal: TradingSignal(
        id: spec.id,
        symbol: spec.symbol,
        name: spec.name,
        market: spec.market,
        direction: direction,
        horizon: Horizon.swing,
        horizonLabel: '1–5 дней',
        score: score,
        entry: entry,
        stopLoss: stopLoss,
        takeProfits: takeProfits,
        priceDecimals: spec.priceDecimals,
        riskReward: rrToTp2.toStringAsFixed(1).replaceAll('.', ','),
        chips: _chips(input, structure, direction),
        note: _note(input, structure, direction, entry),
        factors: [
          for (final c in components)
            SignalFactor(name: c.name, text: c.note, weight: c.weight),
        ],
        events: const [],
        unitRisk: unitRisk,
        unitRiskLabel: '${_round(unitRisk)} ₽ / ${spec.unitRiskSuffix}',
        unitMultiplier: spec.unitMultiplier,
        unitDecimals: spec.unitDecimals,
        unitName: spec.unitName,
        lastPrice: input.lastPrice.toStringAsFixed(spec.priceDecimals).replaceAll('.', ','),
        changeLabel: input.changePercentLabel,
        changeUp: input.changeUp,
        // Порог пуша — 75 (ТЗ §5.3); ниже идея живёт в списке без уведомления.
        status: score >= 75 ? SignalStatus.pushed : SignalStatus.proposed,
        entryIsStop: entryType == EntryType.stopBreak,
        invalidationPrice: stopLoss,
        correlationGroup: _correlationGroup(spec),
        chart: chart,
      ),
    );
  }

  /// Сколько последних свечей отдаётся в график идеи. Окно усечено сознательно:
  /// полная история в памяти каждого сигнала не нужна ни графику, ни телефону.
  static const chartWindow = 72;

  /// График идеи из тех же свечей, по которым считался сигнал.
  ///
  /// Разметка — только реально найденная: уровень слома из структуры, FVG из
  /// индикатора. Ничего декоративного здесь не рисуется.
  SignalChart _chart(
    List<Candle> hourly,
    MarketStructure structure,
    Direction direction,
    double entry,
    double atrHourly,
  ) {
    final start = hourly.length > chartWindow ? hourly.length - chartWindow : 0;
    final window = hourly.sublist(start);

    final long = direction == Direction.long;
    final zones = <ChartZone>[
      for (final gap in fairValueGaps(hourly))
        if (gap.bullish == long &&
            gap.index >= start &&
            (gap.mid - entry).abs() < atrHourly * 2)
          ChartZone(
            from: gap.from,
            to: gap.to,
            startIndex: gap.index - start,
            label: 'FVG',
          ),
    ];

    return SignalChart(
      timeframeLabel: timeframeLabel,
      candles: [
        for (final c in window) ChartCandle(c.open, c.high, c.low, c.close),
      ],
      breakLevel: structure.breakType == StructureBreak.none ? null : structure.breakLevel,
      breakLabel: switch (structure.breakType) {
        StructureBreak.bos => 'BOS',
        StructureBreak.choch => 'CHoCH',
        StructureBreak.none => null,
      },
      // Больше двух зон загромождают график — оставляем ближайшие к текущему краю.
      zones: zones.length > 2 ? zones.sublist(zones.length - 2) : zones,
    );
  }

  Direction? _direction(MarketStructure structure, List<Candle> daily) {
    if (structure.breakType != StructureBreak.none && structure.breakLevel != null) {
      final lastHigh = structure.lastSwingHigh?.price;
      return lastHigh != null && structure.breakLevel == lastHigh
          ? Direction.long
          : Direction.short;
    }
    return switch (structure.trend) {
      StructureTrend.up => Direction.long,
      StructureTrend.down => Direction.short,
      StructureTrend.flat => null,
    };
  }

  /// Есть ли на последнем баре реакция цены в сторону сделки.
  bool _hasTrigger(List<Candle> hourly, bool long) {
    final last = hourly.last;
    final previous = hourly[hourly.length - 2];
    return CandlePatterns.isPinBar(last, bullish: long) ||
        CandlePatterns.isEngulfing(previous, last, bullish: long) ||
        CandlePatterns.isInsideBar(previous, last);
  }

  /// Дописывает в блоки оценки их измеренную историю из аудита факторов.
  ///
  /// Только значимые записи: разница средних на десятке сделок — шум, и
  /// печатать её как знание было бы враньём. Баллы истории не меняют —
  /// перекраивать веса по своему же бэктесту без walk-forward проверки
  /// значит подгонять стратегию под прошлое.
  List<ScoreComponent> _withHistory(
    List<ScoreComponent> components,
    List<FactorEdge>? history,
  ) {
    if (history == null || history.isEmpty) return components;
    final byName = {for (final e in history) e.factor: e};
    return [
      for (final c in components)
        () {
          final edge = byName[c.name];
          if (edge == null || !edge.significant) return c;
          final sign = edge.edge >= 0 ? '+' : '−';
          final value = edge.edge.abs().toStringAsFixed(2).replaceAll('.', ',');
          return ScoreComponent(
            name: c.name,
            points: c.points,
            maxPoints: c.maxPoints,
            note: '${c.note} · история: $sign${value}R/сделку '
                '(${edge.withCount} сделок с фактором)',
          );
        }(),
    ];
  }

  List<ScoreComponent> _score(
    InstrumentInput input,
    MarketStructure structure,
    MarketRegime regime,
    Direction direction,
    double entry,
    double atrHourly,
  ) {
    final long = direction == Direction.long;
    final hourly = input.hourly;
    final closes = [for (final c in hourly) c.close];
    final rsi = rsiWilder(closes);
    final lastRsi = rsi.lastWhere((v) => v != null, orElse: () => null);
    final divergence = findDivergence(hourly, rsi);

    // 1. Структура и слом.
    final aligned = structure.trend == (long ? StructureTrend.up : StructureTrend.down);
    final (structurePoints, structureNote) = switch (structure.breakType) {
      StructureBreak.bos when aligned => (
          weightStructure,
          'BOS 1H на ${_fmt(structure.breakLevel)} по направлению структуры'
        ),
      StructureBreak.bos => (weightStructure * 0.6, 'BOS 1H без подтверждения старшей структурой'),
      StructureBreak.choch => (
          weightStructure * 0.7,
          'CHoCH 1H: слом характера на ${_fmt(structure.breakLevel)}'
        ),
      StructureBreak.none => (
          aligned ? weightStructure * 0.4 : 0.0,
          aligned ? 'Тренд без свежего слома структуры' : 'Структура не поддерживает идею'
        ),
    };

    // 2. Зона входа: близость к уровню и незакрытому FVG.
    final gaps = fairValueGaps(hourly)
        .where((g) => g.bullish == long)
        .where((g) => (g.mid - entry).abs() < atrHourly)
        .toList();
    final distance = (input.lastPrice - entry).abs() / atrHourly;
    final levelPoints = distance <= 0.15
        ? weightLevel
        : distance <= 0.35
            ? weightLevel * 0.6
            : weightLevel * 0.3;
    final levelNote = gaps.isEmpty
        ? 'Ретест уровня ${_fmt(entry)}, цена в ${distance.toStringAsFixed(2)}·ATR от входа'
        : 'Ретест ${_fmt(entry)} и незакрытый FVG ${_fmt(gaps.last.from)}–${_fmt(gaps.last.to)}';

    // 3. Триггер Price Action на последней свече.
    final last = hourly.last;
    final previous = hourly[hourly.length - 2];
    final (paPoints, paNote) = CandlePatterns.isPinBar(last, bullish: long)
        ? (weightPriceAction, 'Пин-бар 1H от уровня')
        : CandlePatterns.isEngulfing(previous, last, bullish: long)
            ? (weightPriceAction * 0.8, 'Поглощение 1H в сторону сделки')
            : CandlePatterns.isInsideBar(previous, last)
                ? (weightPriceAction * 0.4, 'Внутренний бар — сжатие перед импульсом')
                : (0.0, 'Триггерной свечи пока нет — вход лимиткой в зоне');

    // 4. RSI и дивергенция.
    final divergenceAligned =
        divergence == (long ? Divergence.bullish : Divergence.bearish);
    final rsiValue = lastRsi ?? 50;
    final overextended = long ? rsiValue > 72 : rsiValue < 28;
    final (rsiPoints, rsiNote) = divergenceAligned
        ? (weightRsi, 'RSI 1H ${rsiValue.round()}: ${long ? 'бычья' : 'медвежья'} дивергенция')
        : overextended
            ? (0.0, 'RSI 1H ${rsiValue.round()}: вход в зоне перегрева')
            : (weightRsi * 0.6, 'RSI 1H ${rsiValue.round()}: без перегрева, дивергенции нет');

    // 5. Объём, открытый интерес и фандинг.
    final volumes = [for (final c in hourly) c.volume];
    final volumeZ = zScore(volumes);
    var volumePoints = 0.0;
    final volumeNotes = <String>[];
    if (volumeZ != null && volumeZ >= 2) {
      volumePoints += 8;
      volumeNotes.add('объём ${volumeZ.toStringAsFixed(1)}σ');
    } else if (volumeZ != null && volumeZ >= 1) {
      volumePoints += 5;
      volumeNotes.add('объём выше среднего');
    }
    final oi = input.openInterestChangePercent;
    if (oi != null) {
      final oiSupports = long ? oi > 0 : oi > 0; // набор позиции в любую сторону
      if (oiSupports && oi.abs() >= 1) {
        volumePoints += 7;
        volumeNotes.add('OI ${oi > 0 ? '+' : '−'}${oi.abs().toStringAsFixed(1)}% д/д');
      }
    }
    final funding = input.fundingRate;
    if (funding != null) {
      final fundingFuel = long ? funding < 0 : funding > 0;
      if (fundingFuel) {
        volumePoints += 7;
        volumeNotes.add('фандинг ${(funding * 100).toStringAsFixed(3)}% — топливо для сквиза');
      }
    }
    volumePoints = math.min(volumePoints, weightVolume);

    // 6. Соответствие режиму рынка.
    final regimeTrend = regime.forMarket(input.spec.market);
    final (regimePoints, regimeNote) = switch (regimeTrend) {
      StructureTrend.flat => (weightRegime * 0.55, 'Режим рынка нейтральный'),
      StructureTrend.up => long
          ? (weightRegime, 'Идея по направлению рынка')
          : (0.0, 'Шорт против растущего рынка'),
      StructureTrend.down => long
          ? (0.0, 'Лонг против падающего рынка')
          : (weightRegime, 'Идея по направлению рынка'),
    };

    return [
      ScoreComponent(
        name: 'Структура · SMC',
        points: structurePoints,
        maxPoints: weightStructure,
        note: structureNote,
      ),
      ScoreComponent(
        name: 'Зона входа',
        points: levelPoints,
        maxPoints: weightLevel,
        note: levelNote,
      ),
      ScoreComponent(
        name: 'Price Action',
        points: paPoints,
        maxPoints: weightPriceAction,
        note: paNote,
      ),
      ScoreComponent(name: 'RSI(14)', points: rsiPoints, maxPoints: weightRsi, note: rsiNote),
      ScoreComponent(
        name: 'Объём / OI',
        points: volumePoints,
        maxPoints: weightVolume,
        note: volumeNotes.isEmpty ? 'Объём и OI без аномалий' : volumeNotes.join(' · '),
      ),
      ScoreComponent(
        name: 'Режим рынка',
        points: regimePoints,
        maxPoints: weightRegime,
        note: regimeNote,
      ),
    ];
  }

  List<String> _chips(InstrumentInput input, MarketStructure structure, Direction direction) {
    final chips = <String>[];
    final long = direction == Direction.long;
    switch (structure.breakType) {
      case StructureBreak.bos:
        chips.add('BOS 1H');
      case StructureBreak.choch:
        chips.add('CHoCH 1H');
      case StructureBreak.none:
        break;
    }
    if (fairValueGaps(input.hourly).any((g) => g.bullish == long)) chips.add('FVG');
    final rsi = rsiWilder([for (final c in input.hourly) c.close]);
    if (findDivergence(input.hourly, rsi) == (long ? Divergence.bullish : Divergence.bearish)) {
      chips.add('RSI-див');
    }
    final oi = input.openInterestChangePercent;
    if (oi != null && oi.abs() >= 1) {
      chips.add('OI ${oi > 0 ? '+' : '−'}${oi.abs().toStringAsFixed(1)}%');
    }
    final funding = input.fundingRate;
    if (funding != null && funding.abs() >= 0.00005) {
      chips.add('Funding ${funding < 0 ? '−' : '+'}');
    }
    return chips;
  }

  String _note(
    InstrumentInput input,
    MarketStructure structure,
    Direction direction,
    double entry,
  ) {
    final long = direction == Direction.long;
    final side = long ? 'Лонг' : 'Шорт';
    final breakText = switch (structure.breakType) {
      StructureBreak.bos => 'слом структуры (BOS) на ${_fmt(structure.breakLevel)}',
      StructureBreak.choch => 'слом характера (CHoCH) на ${_fmt(structure.breakLevel)}',
      StructureBreak.none => 'структура ${long ? 'растущая' : 'падающая'}',
    };
    return '$side от ретеста ${_fmt(entry)}: $breakText на 1H. '
        'Инвалидация — закрепление за стопом. '
        'План ведения: фиксация ${takeProfitShares.join('/')}% по тейкам, '
        'после TP1 стоп остатка в безубыток, выход по времени через 5 торговых '
        'дней, если цели не достигнуты. '
        'Расчёт выполнен на устройстве по публичным данным биржи, без LLM-разбора.';
  }

  String? _correlationGroup(InstrumentSpec spec) => correlationGroup(spec);

  String _fmt(double? value) =>
      value == null ? '—' : value.toStringAsFixed(value.abs() < 10 ? 2 : 0).replaceAll('.', ',');

  String _round(double value) =>
      value.round().toString().replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ' ');
}
