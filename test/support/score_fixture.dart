import 'package:signalai/domain/idea/quality_score.dart';

/// Оценка ровно на [value] баллов по модели engine-ТЗ §15.1.
///
/// Итог у engine-ТЗ приходит **с сервера** и не пересчитывается на клиенте,
/// поэтому в фикстуре он задаётся прямо. Компоненты добавляются для полноты
/// разбора: экраны показывают их, и тест на пустом списке проверял бы не то,
/// что видит владелец.
QualityScore scoreOf(int value, {double dataQuality = 1.0}) {
  final fraction = (value / 100).clamp(0.0, 1.0);
  return QualityScore(
    total: value.toDouble(),
    dataQuality: dataQuality,
    contributions: [
      for (final kind in ScoreComponentKind.values)
        ScoreContribution(
          kind: kind,
          value: fraction * 100,
          effectiveWeight: kind.weight,
          points: kind.weight * fraction * 100,
          detail: '',
          evidenceId: 'regime',
        ),
    ],
  );
}

/// Оценка с неизмеренным компонентом — проверка того, что «не посчитано» не
/// читается как «плохо».
QualityScore scoreWithMissing(int value, ScoreComponentKind missing) {
  final base = scoreOf(value);
  return QualityScore(
    total: base.total,
    dataQuality: 0.8,
    contributions: [
      for (final c in base.contributions)
        if (c.kind == missing)
          ScoreContribution(
            kind: c.kind,
            value: 0,
            effectiveWeight: c.effectiveWeight,
            points: 0,
            status: MeasurementStatus.missing,
            detail: 'источник не ответил',
          )
        else
          c,
    ],
  );
}
