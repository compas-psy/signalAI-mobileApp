/// Фактор оценки идеи с весом из ТЗ §14.2.
///
/// Веса заданы заказчиком и не подбираются: подбор шести весов на нашей
/// выборке — гарантированная подгонка. Сумма ровно 100.
enum ScoreFactor {
  marketRegime('Market regime', 15, 'Режим рынка на 1D: тренд, флэт, разворот'),
  multiTfStructure('Multi-TF structure', 15, 'Согласованность 1D / 4H / 1H'),
  zoneQuality('Zone quality', 15, 'Качество зоны входа: свежесть, реакция, ширина'),
  entryTrigger('Entry trigger / Price Action', 15, 'Триггерная свеча в контекстной зоне'),
  volumeOi('Volume and OI', 15, 'Подтверждение объёмом и открытым интересом'),
  riskReward('R/R and room', 10, 'Отношение к целям и запас хода до препятствия'),
  liquidity('Liquidity', 5, 'Спред, глубина, оборот'),
  eventRisk('Event risk', 10, 'Близость события и его политика');

  const ScoreFactor(this.label, this.weight, this.meaning);

  final String label;

  /// Вес фактора в итоговой оценке. Сумма всех — 100 (ТЗ §14.2).
  final int weight;

  final String meaning;

  static int get totalWeight =>
      ScoreFactor.values.fold(0, (sum, f) => sum + f.weight);
}

/// Вклад одного фактора: доля выполнения и то, чем она обоснована.
class FactorContribution {
  const FactorContribution({
    required this.factor,
    required this.fraction,
    required this.note,
    this.evidenceId,
  });

  final ScoreFactor factor;

  /// Насколько фактор выполнен: 0…1.
  final double fraction;

  /// Человеческое объяснение — то же, что покажет карточка.
  final String note;

  /// Ссылка на доказательство, отрисованное на графике (ТЗ §9.1).
  ///
  /// Тезис и график обязаны ссылаться на один и тот же объект: иначе текст
  /// говорит одно, а разметка — другое.
  final String? evidenceId;

  /// Баллы фактора в итоговой оценке.
  double get points => factor.weight * fraction.clamp(0.0, 1.0);
}

/// Итоговая оценка качества идеи (ТЗ §14.2).
///
/// Это **quality score, а не вероятность**. ТЗ §14.3 запрещает превращать
/// оценку в вероятность до накопления калиброванной выборки: показывать
/// «73% успеха» на десятке сделок значит выдавать шум за знание.
class QualityScore {
  const QualityScore({required this.contributions});

  final List<FactorContribution> contributions;

  /// Оценка 0…100.
  int get value => contributions
      .fold(0.0, (sum, c) => sum + c.points)
      .round()
      .clamp(0, 100);

  /// Риск на сделку в процентах капитала (ТЗ §14.2).
  ///
  /// Ниже 65 идея вообще не показывается; 65–74 — только наблюдение без
  /// права входа. Hard limits риск-движка имеют приоритет над этой таблицей.
  double? get riskPercent {
    final score = value;
    if (score < 75) return null;
    if (score < 80) return 0.50;
    if (score < 90) return 0.75;
    return 1.00;
  }

  /// Минимальная оценка, при которой идея попадает в ленту.
  static const minimumToShow = 65;

  /// Ниже этого порога вход запрещён независимо от прочего.
  static const minimumToTrade = 75;

  /// Может ли идея дойти до состояния Triggered.
  bool get tradable => value >= minimumToTrade;

  /// Подпись, честная относительно природы числа.
  String get label => 'Quality score $value/100';

  /// Факторы, просевшие сильнее прочих, — их и надо объяснять первыми.
  List<FactorContribution> get weakest {
    final sorted = [...contributions]
      ..sort((a, b) => (a.fraction).compareTo(b.fraction));
    return sorted.take(3).toList();
  }
}
