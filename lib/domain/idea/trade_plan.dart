import '../enums.dart';

/// Тип заявки на вход.
enum PlanOrderType {
  limit('Лимит', 'Вход по цене не хуже границы зоны'),
  stopLimit('Стоп-лимит', 'Вход по пробою с ограничением проскальзывания'),
  market('Рынок', 'Вход по любой цене — только при высокой ликвидности');

  const PlanOrderType(this.label, this.meaning);

  final String label;
  final String meaning;
}

/// Цель плана: цена, доля закрытия и правило после неё (ТЗ §11.2, §21.1).
class PlanTarget {
  const PlanTarget({
    required this.name,
    required this.price,
    required this.fraction,
    required this.afterFill,
  });

  /// TP1 / TP2 / TP3.
  final String name;
  final double price;

  /// Какая доля позиции закрывается: 0,4 / 0,4 / 0,2 по ТЗ §21.1.
  final double fraction;

  /// Что делает движок после исполнения (§21.1).
  final String afterFill;
}

/// Правила сопровождения стопа (ТЗ §21.1) — часть подписанного плана.
///
/// Владелец подписывает не только вход, но и то, что произойдёт дальше:
/// иначе «перенести стоп в безубыток» остаётся обещанием на словах.
const defaultStopManagement = <String>[
  'Вход исполнен — сразу выставляются стоп и TP1–TP3',
  'TP1 — закрыть 40%, стоп в безубыток с учётом комиссий',
  'TP2 — закрыть 40%, стоп по подтверждённой структуре 1H',
  'TP3 — закрыть 20% либо ручной разбор по политике',
  'Сбой данных — активные заявки у брокера не трогаем, только сверка',
];

/// Торговый план (ТЗ §11.2, §17).
///
/// Все параметры заявок прямые: цены, доли, объём. План неизменяем — при
/// изменении логики создаётся новая версия (ТЗ §17.1), а [hash] позволяет
/// финальной проверке отличить показанный план от пересчитанного.
class TradePlan {
  const TradePlan({
    required this.instrumentId,
    required this.direction,
    required this.orderType,
    required this.entryLow,
    required this.entryHigh,
    required this.maxSlippagePercent,
    required this.stop,
    required this.targets,
    required this.quantity,
    required this.lotSize,
    required this.valuePerPoint,
    required this.riskRubles,
    required this.riskPercent,
    required this.marginEstimate,
    required this.expiry,
    required this.strategyVersion,
    this.quantityStep = 1,
    this.quantityUnit = '',
    this.quoteCurrency = 'RUB',
    this.quoteRateRub,
    this.quoteNote = '',
    this.stopManagement = defaultStopManagement,
    this.invalidation = const [],
  });

  final String instrumentId;
  final Direction direction;
  final PlanOrderType orderType;

  /// Зона входа. Заявка вне зоны — уже другая сделка (ТЗ §11.1).
  final double entryLow;
  final double entryHigh;

  /// Допустимое проскальзывание в процентах от цены входа.
  final double maxSlippagePercent;

  final double stop;
  final List<PlanTarget> targets;

  /// Объём в **номинале инструмента**, уже округлённый вниз к его шагу.
  ///
  /// Дробный намеренно. У бессрочного контракта на эфир шаг объёма 0,001
  /// монеты, и целое число здесь было бы не округлением, а другой позицией:
  /// 0,348 ETH превращались в ноль, а «28» — в двадцать восемь монет, то
  /// есть в два миллиона рублей при депозите в один.
  final double quantity;

  /// Шаг объёма — им же задаётся, сколько знаков показывать.
  final double quantityStep;

  /// Чем меряется объём: «ETH», «контр.», «шт.».
  final String quantityUnit;

  final int lotSize;

  /// Стоимость движения цены на один пункт для одной единицы, в валюте
  /// котировки инструмента.
  final double valuePerPoint;

  /// Валюта, в которой котируется инструмент. Цены плана — в ней.
  final String quoteCurrency;

  /// Курс этой валюты к рублю, по которому посчитан [riskRubles]. null —
  /// инструмент рублёвый либо курс неизвестен и денег в рублях нет.
  final double? quoteRateRub;

  /// Подпись под курсом: откуда он и когда снят.
  final String quoteNote;

  /// Риск в рублях. Именно в рублях: бюджет задан ими, и убыток по стопу
  /// считается по этому числу. Цены при этом остаются в валюте котировки.
  final double riskRubles;
  final double riskPercent;
  final double marginEstimate;

  /// Момент, после которого план исполнять нельзя.
  final DateTime expiry;

  final String strategyVersion;
  final List<String> stopManagement;

  /// Жёсткие условия, при которых замысел считается сломанным (ТЗ §9).
  final List<String> invalidation;

  /// Опорная цена входа — середина зоны.
  double get entry => (entryLow + entryHigh) / 2;

  /// Расстояние до стопа в пунктах цены. Это знаменатель всего сайзинга.
  double get priceRisk => (entry - stop).abs();

  /// Риск на одну единицу инструмента в рублях.
  double get riskPerUnit => priceRisk * valuePerPoint;

  /// R/R до указанной цели.
  double rrTo(PlanTarget target) {
    if (priceRisk == 0) return 0;
    final reward = direction.isLong ? target.price - entry : entry - target.price;
    return reward / priceRisk;
  }

  /// R/R до TP2 — именно по нему ТЗ §11.1 ставит порог исполнения.
  double get rrToSecondTarget =>
      targets.length >= 2 ? rrTo(targets[1]) : (targets.isEmpty ? 0 : rrTo(targets.first));

  /// Взвешенный по долям результат при исполнении всех целей, R.
  double get weightedR {
    var sum = 0.0;
    for (final t in targets) {
      sum += t.fraction * rrTo(t);
    }
    return sum;
  }

  /// Минимальный R/R для исполнения (ТЗ §20: execution floor 1:1,8).
  static const executionFloorRr = 1.8;

  /// Целевой R/R стратегии (ТЗ §20: target 1:2).
  static const targetRr = 2.0;

  bool isExpired(DateTime now) => !now.isBefore(expiry);

  /// Причины, по которым план нельзя подписать (ТЗ §11.1, §13.1).
  ///
  /// Возвращается список, а не bool: владельцу нужно видеть, что именно не
  /// сходится, иначе кнопка просто «не нажимается» без объяснения.
  List<String> structuralBlockers() {
    final issues = <String>[];

    if (entryLow > entryHigh) {
      issues.add('границы зоны входа перепутаны');
    }
    if (priceRisk <= 0) {
      issues.add('стоп совпадает с ценой входа — риск не определён');
    }
    // Стоп обязан стоять за входом по направлению сделки. Стоп «выше входа»
    // в лонге — не защита, а гарантированное закрытие.
    if (direction.isLong && stop >= entryLow) {
      issues.add('в лонге стоп должен быть ниже зоны входа');
    }
    if (!direction.isLong && stop <= entryHigh) {
      issues.add('в шорте стоп должен быть выше зоны входа');
    }
    if (targets.isEmpty) {
      issues.add('нет ни одной цели');
    }

    for (final t in targets) {
      final beyond = direction.isLong ? t.price > entryHigh : t.price < entryLow;
      if (!beyond) {
        issues.add('${t.name} стоит не по направлению сделки');
      }
    }
    // Цели обязаны идти по возрастанию расстояния: TP2 ближе TP1 означает
    // ошибку разметки, а доли закрытия при этом посчитаны по порядку.
    for (var i = 1; i < targets.length; i++) {
      final prev = rrTo(targets[i - 1]);
      final cur = rrTo(targets[i]);
      if (cur <= prev) {
        issues.add('${targets[i].name} не дальше ${targets[i - 1].name}');
      }
    }

    final fractions = targets.fold(0.0, (s, t) => s + t.fraction);
    if (targets.isNotEmpty && (fractions - 1.0).abs() > 0.001) {
      issues.add('доли закрытия дают ${(fractions * 100).round()}% вместо 100%');
    }

    // Кратность проверяется шагу объёма, а не лоту: у крипты шаг 0,001
    // монеты, и «кратно единице» забраковало бы каждую верную позицию.
    final step = quantityStep > 0 ? quantityStep : lotSize.toDouble();
    if (step > 0 && quantity < step) {
      issues.add('объём меньше минимального — идея остаётся информационной');
    }
    if (step > 0 && quantity >= step) {
      // Допуск нужен: 0,348 / 0,001 в double даёт 347,99999999999994, и
      // точная проверка остатка забраковала бы верный объём.
      final steps = quantity / step;
      if ((steps - steps.roundToDouble()).abs() > 1e-6) {
        issues.add('объём не кратен шагу $step');
      }
    }
    if (riskRubles <= 0 || riskPercent <= 0) {
      issues.add('риск не рассчитан');
    }
    if (rrToSecondTarget < executionFloorRr) {
      issues.add(
        'R/R до TP2 ${_r(rrToSecondTarget)} ниже порога ${_r(executionFloorRr)}',
      );
    }

    return issues;
  }

  /// Отпечаток плана. Финальная проверка сверяет его с показанным (ТЗ §11.1).
  ///
  /// Считается детерминированно (FNV-1a по канонической строке), чтобы
  /// совпадать между запусками: hashCode строк в Dart для этого не годится.
  String get hash {
    final buffer = StringBuffer()
      ..write(instrumentId)
      ..write('|')
      ..write(direction.name)
      ..write('|')
      ..write(orderType.name)
      ..write('|')
      ..write(_n(entryLow))
      ..write('|')
      ..write(_n(entryHigh))
      ..write('|')
      ..write(_n(stop))
      ..write('|')
      ..write(_n(quantity))
      ..write('|')
      ..write(_n(riskRubles))
      ..write('|')
      ..write(strategyVersion)
      ..write('|')
      ..write(expiry.toUtc().toIso8601String());
    for (final t in targets) {
      buffer
        ..write('|')
        ..write(t.name)
        ..write(':')
        ..write(_n(t.price))
        ..write(':')
        ..write(_n(t.fraction));
    }
    return _fnv1a(buffer.toString());
  }

  TradePlan copyWith({
    double? quantity,
    double? riskRubles,
    double? riskPercent,
    double? marginEstimate,
  }) =>
      TradePlan(
        instrumentId: instrumentId,
        direction: direction,
        orderType: orderType,
        entryLow: entryLow,
        entryHigh: entryHigh,
        maxSlippagePercent: maxSlippagePercent,
        stop: stop,
        targets: targets,
        quantity: quantity ?? this.quantity,
        quantityStep: quantityStep,
        quantityUnit: quantityUnit,
        lotSize: lotSize,
        valuePerPoint: valuePerPoint,
        quoteCurrency: quoteCurrency,
        quoteRateRub: quoteRateRub,
        quoteNote: quoteNote,
        riskRubles: riskRubles ?? this.riskRubles,
        riskPercent: riskPercent ?? this.riskPercent,
        marginEstimate: marginEstimate ?? this.marginEstimate,
        expiry: expiry,
        strategyVersion: strategyVersion,
        stopManagement: stopManagement,
        invalidation: invalidation,
      );

  /// Сколько знаков нужно объёму, чтобы шаг был виден целиком.
  ///
  /// Считается из шага, а не задаётся константой: у контракта шаг 1 и знаков
  /// не нужно вовсе, у эфира 0,001 — нужно три. Округлить 0,348 до целых
  /// значит показать ноль там, где позиция есть.
  int get quantityDecimals {
    var step = quantityStep;
    if (step <= 0 || step >= 1) return 0;
    var decimals = 0;
    while (decimals < 8 && (step - step.roundToDouble()).abs() > 1e-9) {
      step *= 10;
      decimals++;
    }
    return decimals;
  }

  static String _n(double v) => v.toStringAsFixed(6);

  static String _r(double v) => v.toStringAsFixed(2).replaceAll('.', ',');

  /// FNV-1a, 32 бита. Достаточно, чтобы поймать подмену плана между показом
  /// и подтверждением; это не криптографическая подпись и ею не называется.
  static String _fnv1a(String input) {
    var hash = 0x811c9dc5;
    for (final unit in input.codeUnits) {
      hash ^= unit;
      hash = (hash * 0x01000193) & 0xffffffff;
    }
    return hash.toRadixString(16).padLeft(8, '0');
  }
}

/// Бюджет риска на сделку (ТЗ §20, §20.1).
///
/// Итоговый бюджет — минимум из всех остатков. Hard limits имеют приоритет
/// над таблицей score→risk: высокая оценка не пробивает дневной лимит.
class RiskBudget {
  const RiskBudget({
    required this.scoreRiskPercent,
    required this.remainingDailyPercent,
    required this.remainingWeeklyPercent,
    required this.remainingMonthlyPercent,
    required this.remainingOpenRiskPercent,
    required this.remainingClusterPercent,
  });

  /// Жёсткие лимиты engine-ТЗ §17.
  ///
  /// Числа взяты у engine-ТЗ, а не у UX-ТЗ §20, где стояли 3 / 5 / 10% и
  /// кластер 1,25%. Расхождение разобрано в docs/TZ_PRIORITY.md: на числах
  /// побеждает engine-ТЗ, и здесь это не вкусовщина — более мягкий лимит
  /// разрешает просадку, которую движок считает недопустимой, а разница
  /// между 6% и 10% месячного убытка это разница между «продолжаем» и
  /// «остановились».
  static const dailyLossPercent = 1.5;
  static const weeklyLossPercent = 3.5;
  static const monthlyLossPercent = 6.0;
  static const openRiskPercent = 2.0;
  static const clusterRiskPercent = 1.0;

  final double scoreRiskPercent;
  final double remainingDailyPercent;
  final double remainingWeeklyPercent;
  final double remainingMonthlyPercent;
  final double remainingOpenRiskPercent;
  final double remainingClusterPercent;

  /// Что именно ограничивает бюджет — это и показывается владельцу.
  ({String name, double percent}) get binding {
    final all = <({String name, double percent})>[
      (name: 'оценка идеи', percent: scoreRiskPercent),
      (name: 'дневной лимит', percent: remainingDailyPercent),
      (name: 'недельный лимит', percent: remainingWeeklyPercent),
      (name: 'месячный лимит', percent: remainingMonthlyPercent),
      (name: 'открытый риск', percent: remainingOpenRiskPercent),
      (name: 'риск кластера', percent: remainingClusterPercent),
    ];
    var min = all.first;
    for (final item in all) {
      if (item.percent < min.percent) min = item;
    }
    return min;
  }

  /// Итоговый бюджет в процентах капитала, не ниже нуля.
  double get percent => binding.percent < 0 ? 0 : binding.percent;

  bool get blocked => percent <= 0;
}

/// Расчёт объёма по ТЗ §20.1.
abstract final class PlanSizing {
  /// Объём в единицах инструмента, округлённый вниз до кратности лота.
  ///
  /// Округление всегда вниз: превысить бюджет риска нельзя даже на один лот.
  static int quantity({
    required double riskBudgetRubles,
    required double priceRisk,
    required double valuePerPoint,
    required int lotSize,
  }) {
    if (riskBudgetRubles <= 0 || priceRisk <= 0 || valuePerPoint <= 0) return 0;
    if (lotSize <= 0) return 0;
    final perUnit = priceRisk * valuePerPoint;
    final raw = riskBudgetRubles / perUnit;
    if (raw < lotSize) return 0;
    return (raw / lotSize).floor() * lotSize;
  }

  /// Фактический риск подобранного объёма — он всегда не больше бюджета.
  static double riskOf({
    required int quantity,
    required double priceRisk,
    required double valuePerPoint,
  }) =>
      quantity * priceRisk * valuePerPoint;
}
