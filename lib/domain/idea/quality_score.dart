/// Оценка идеи по engine-ТЗ §15.1: одиннадцать компонентов и множитель
/// качества данных.
///
/// Раньше здесь было восемь факторов UX-ТЗ §14.2 — приложение писалось до
/// появления engine-ТЗ v1.0. Разошлись не только имена: у engine-ТЗ веса
/// заданы долями, компоненты умеют быть **неприменимыми** (открытого
/// интереса у акции не бывает) и **неизмеренными** (бывает, но запрос упал),
/// а вся сумма умножается на качество данных. Восьмифакторная модель это
/// различие выразить не могла, и отказ источника выглядел в ней ровно так же,
/// как измеренная слабость. По docs/TZ_PRIORITY.md на числах побеждает
/// engine-ТЗ.
///
/// Считает оценку **сервер**. Здесь она только переносится и показывается:
/// вторая реализация тех же формул на Dart разошлась бы с серверной на
/// третьем округлении, и владелец увидел бы два разных балла у одной идеи.
library;

/// Компонент оценки §15.1. Порядок и веса — из ТЗ, подбору не подлежат.
enum ScoreComponentKind {
  regimeFit('regime_fit', 'Соответствие режиму рынка', 0.16),
  marketStructure('market_structure', 'Структура рынка', 0.14),
  setupQuality('setup_quality', 'Качество сетапа', 0.14),
  triggerQuality('trigger_quality', 'Качество триггера', 0.12),
  derivativesConfirmation(
      'derivatives_confirmation', 'Подтверждение производными', 0.10),
  volumeConfirmation('volume_confirmation', 'Подтверждение объёмом', 0.08),
  liquidityQuality('liquidity_quality', 'Ликвидность', 0.07),
  volatilityFit('volatility_fit', 'Соответствие волатильности', 0.06),
  levelConfluence('level_confluence', 'Схождение уровней', 0.06),
  riskRewardQuality('risk_reward_quality', 'Качество соотношения риска', 0.04),
  executionQuality('execution_quality', 'Качество исполнения', 0.03);

  const ScoreComponentKind(this.wire, this.label, this.weight);

  /// Имя в контракте §18. Разбор идёт по нему, а не по порядку в списке.
  final String wire;
  final String label;

  /// Номинальный вес из ТЗ. Фактический может отличаться: вес неприменимого
  /// компонента перераспределяется между остальными.
  final double weight;

  static ScoreComponentKind? parse(String? wire) {
    for (final kind in values) {
      if (kind.wire == wire) return kind;
    }
    return null;
  }
}

/// Состояние измерения. Разница между двумя последними — деньги, а не
/// педантизм: неприменимое не портит оценку, неизмеренное портит и обязано
/// быть видно.
enum MeasurementStatus {
  measured('измерено'),
  notApplicable('неприменимо'),
  missing('не измерено');

  const MeasurementStatus(this.label);
  final String label;

  static MeasurementStatus parse(Object? raw) => switch (raw) {
        'not_applicable' => MeasurementStatus.notApplicable,
        'missing' => MeasurementStatus.missing,
        false => MeasurementStatus.missing,
        _ => MeasurementStatus.measured,
      };
}

/// Вклад одного компонента в оценку.
class ScoreContribution {
  const ScoreContribution({
    required this.kind,
    required this.value,
    required this.effectiveWeight,
    required this.points,
    this.status = MeasurementStatus.measured,
    this.detail = '',
    this.evidenceId,
  });

  final ScoreComponentKind kind;

  /// Значение компонента 0…100.
  final double value;

  /// Вес после перераспределения. Отличается от номинального, когда часть
  /// компонентов неприменима.
  final double effectiveWeight;

  /// Баллы в итоговой оценке.
  final double points;

  final MeasurementStatus status;
  final String detail;

  /// Ссылка на доказательство (§9.1). Тезис, оценка и график обязаны
  /// ссылаться на один объект, иначе текст говорит одно, а разметка другое.
  final String? evidenceId;

  bool get isMeasured => status == MeasurementStatus.measured;

  static ScoreContribution? fromJson(Map<String, dynamic> j) {
    final kind = ScoreComponentKind.parse(j['name'] as String?);
    if (kind == null) return null;
    return ScoreContribution(
      kind: kind,
      value: _double(j['value']),
      effectiveWeight: _double(j['weight']),
      points: _double(j['points']),
      status: MeasurementStatus.parse(j['measured'] ?? j['status']),
      detail: j['detail'] as String? ?? '',
      evidenceId: j['evidence_id'] as String?,
    );
  }
}

double _double(Object? v) => switch (v) {
      num n => n.toDouble(),
      String s => double.tryParse(s) ?? 0,
      _ => 0,
    };

/// Итоговая оценка §15.1.
///
/// Это **оценка качества, а не вероятность**. Вероятность у engine-ТЗ своя,
/// со строгим определением (P(TP1 раньше SL в пределах горизонта)), и она
/// приходит отдельным блоком. Смешивать их нельзя: «73 балла» и «73%
/// успеха» — утверждения разной силы, и второе движок пока не вправе делать.
class QualityScore {
  const QualityScore({
    required this.contributions,
    required this.total,
    this.dataQuality = 1.0,
  });

  /// Пустая оценка — для идей, у которых разбор ещё не приехал.
  const QualityScore.empty()
      : contributions = const [],
        total = 0,
        dataQuality = 1.0;

  final List<ScoreContribution> contributions;

  /// Итог 0…100, уже умноженный на качество данных. Приходит с сервера.
  final double total;

  /// Множитель качества данных 0,5…1,0 (§15.1). Ниже единицы — в оценке есть
  /// компоненты, которые не удалось измерить.
  final double dataQuality;

  int get value => total.round().clamp(0, 100);

  /// Риск на сделку в долях капитала (engine-ТЗ §17).
  ///
  /// Две ступени: базовые 0,5% и 0,75% для A-grade от 80 баллов. Ступени 1%
  /// из UX-ТЗ здесь нет — расхождение разобрано в docs/TZ_PRIORITY.md, и на
  /// числах побеждает engine-ТЗ. Жёсткие лимиты риск-движка всё равно
  /// приоритетнее этой таблицы.
  double? get riskPercent {
    if (value < minimumToTrade) return null;
    return value >= aGradeScore ? 0.75 : 0.50;
  }

  /// Ниже — идея не показывается вовсе.
  static const minimumToShow = 65;

  /// Ниже — вход запрещён независимо от прочего.
  static const minimumToTrade = 75;

  /// Балл, с которого идея считается A-grade (§17).
  static const aGradeScore = 80;

  bool get tradable => value >= minimumToTrade;

  /// Подпись, честная относительно природы числа.
  String get label => 'Оценка $value/100';

  /// Есть ли в оценке неизмеренное. Такую идею нельзя читать как «слабую» —
  /// её нужно читать как «недосчитанную».
  bool get hasUnmeasured =>
      contributions.any((c) => c.status == MeasurementStatus.missing);

  List<ScoreContribution> get unmeasured => contributions
      .where((c) => c.status == MeasurementStatus.missing)
      .toList(growable: false);

  /// Компоненты, просевшие сильнее прочих, — их и объяснять первыми.
  /// Неприменимые не считаются слабыми: их отсутствие не изъян идеи.
  List<ScoreContribution> get weakest {
    final measured = contributions
        .where((c) => c.status != MeasurementStatus.notApplicable)
        .toList()
      ..sort((a, b) => a.value.compareTo(b.value));
    return measured.take(3).toList(growable: false);
  }

  /// Компоненты в порядке фактического вклада — так их и показывает разбор.
  List<ScoreContribution> get byContribution {
    final sorted = [...contributions]
      ..sort((a, b) => b.points.compareTo(a.points));
    return sorted;
  }

  static QualityScore fromJson(Map<String, dynamic> j) {
    final raw = (j['components'] as List<dynamic>? ?? const []);
    final parsed = <ScoreContribution>[];
    for (final item in raw) {
      final c = ScoreContribution.fromJson(item as Map<String, dynamic>);
      if (c != null) parsed.add(c);
    }
    return QualityScore(
      contributions: parsed,
      total: _double(j['total']),
      dataQuality: j['data_quality'] == null ? 1.0 : _double(j['data_quality']),
    );
  }
}
