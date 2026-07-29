import 'package:flutter/widgets.dart';

import '../../domain/idea/evidence.dart';
import '../../domain/idea/final_check.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/quality_score.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'segmented.dart';
import 'vector_icon.dart';

/// Из чего собрана оценка (engine-ТЗ §15.1).
///
/// Показывает все одиннадцать компонентов, включая неизмеренные и
/// неприменимые: оценка 60 из ста без разбора читается как «слабая идея»,
/// хотя на деле это может быть «два компонента не посчитаны». Разница
/// принципиальная, и различить её обязано приложение, а не владелец.
///
/// Неприменимое и неизмеренное разведены намеренно. Открытого интереса у
/// акции не бывает — это не изъян идеи, и вес такого компонента ушёл
/// остальным. Открытый интерес у фьючерса бывает, но не приехал — это дыра,
/// и она обязана быть видна.
class ScoreBreakdownCard extends StatelessWidget {
  const ScoreBreakdownCard({super.key, required this.score});

  final QualityScore score;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Из чего собрана оценка')),
                Text(score.label, style: T.mono(11, color: C.accent)),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              // ТЗ §14.3: превращать оценку в вероятность до калибровки нельзя.
              'Это оценка качества сетапа, а не вероятность прибыли. '
              'Вероятность у движка своя и определена строго.',
              style: T.body(11, color: C.faint, height: 1.4),
            ),
            if (score.dataQuality < 1.0) ...[
              const SizedBox(height: 6),
              Text(
                'Качество данных ${(score.dataQuality * 100).round()}% — '
                'оценка умножена на этот множитель (§15.1).',
                style: T.body(11, color: C.warning, height: 1.4),
              ),
            ],
            const SizedBox(height: 11),
            // Прототип раскладывает разбор плитками, а не строками: одиннадцать
            // компонентов колонкой — это экран прокрутки, на котором ни один
            // не сравнивается ни с одним.
            TileGrid(
              tiles: [
                for (final c in score.byContribution) _FactorTile(contribution: c),
              ],
              minTileWidth: 132,
            ),
            const SizedBox(height: 12),
            _riskLine(),
          ],
        ),
      );

  Widget _riskLine() {
    final risk = score.riskPercent;
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: C.inset,
        borderRadius: BorderRadius.circular(R.inset),
      ),
      child: Text(
        risk == null
            ? 'Оценка ниже ${QualityScore.minimumToTrade}: идея остаётся '
                'наблюдением, риск на неё не выделяется.'
            : 'Оценка допускает риск ${_pct(risk)} капитала. Жёсткие лимиты '
                'имеют приоритет над этой цифрой.',
        style: T.body(11, color: risk == null ? C.warning : C.muted, height: 1.45),
      ),
    );
  }

  static String _pct(double v) =>
      '${v.toStringAsFixed(2).replaceAll('.', ',')}%';
}

class _FactorTile extends StatelessWidget {
  const _FactorTile({required this.contribution});

  final ScoreContribution contribution;

  /// Доля выполнения компонента: значение приходит в шкале 0…100.
  double get _fraction => (contribution.value / 100).clamp(0.0, 1.0);

  @override
  Widget build(BuildContext context) {
    final status = contribution.status;
    final measured = status == MeasurementStatus.measured;
    final color = switch (status) {
      MeasurementStatus.notApplicable => C.faint,
      MeasurementStatus.missing => C.warning,
      MeasurementStatus.measured when _fraction >= 0.75 => C.green,
      MeasurementStatus.measured when _fraction >= 0.4 => C.accent,
      _ => C.warning,
    };
    return Container(
      padding: const EdgeInsets.fromLTRB(10, 9, 10, 10),
      decoration: BoxDecoration(
        color: C.inset,
        border: Border.all(color: C.divider),
        borderRadius: BorderRadius.circular(R.inset),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            contribution.kind.label,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: T.body(11, weight: 700, height: 1.3),
          ),
          const SizedBox(height: 5),
          Text(
            // Знаменатель — фактический вес: у неприменимого компонента вес
            // перераспределён, и показывать номинальный значило бы обещать
            // баллы, которых в этой оценке не существует.
            switch (status) {
              MeasurementStatus.notApplicable => 'неприменимо',
              MeasurementStatus.missing => 'не измерено',
              MeasurementStatus.measured => '${contribution.points.round()} из '
                  '${(contribution.effectiveWeight * 100).round()}',
            },
            style: T.mono(11, weight: 600, color: color),
          ),
          const SizedBox(height: 6),
          ClipRRect(
            borderRadius: BorderRadius.circular(R.pill),
            child: SizedBox(
              height: 5,
              child: Stack(
                children: [
                  const ColoredBox(color: C.chip, child: SizedBox.expand()),
                  FractionallySizedBox(
                    widthFactor: measured ? _fraction : 0.0,
                    child: ColoredBox(color: color, child: const SizedBox.expand()),
                  ),
                ],
              ),
            ),
          ),
          // Пояснение остаётся у неизмеренного и неприменимого: там оно
          // отвечает на вопрос «почему», а у измеренного повторяет полоску.
          if (!measured && contribution.detail.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(contribution.detail, style: T.body(10, color: C.muted, height: 1.35)),
          ],
        ],
      ),
    );
  }
}

/// Тезис идеи с доказательствами (ТЗ §9, зона Thesis; §9.1).
///
/// Каждая строка — доказательство, участвовавшее в оценке. Тап подсвечивает
/// ровно те метки на графике, на которые оно ссылается: текст и картинка
/// обязаны говорить одно и то же, и проверить это должно быть можно пальцем,
/// а не на слово.
class ThesisCard extends StatelessWidget {
  const ThesisCard({
    super.key,
    required this.idea,
    required this.selectedEvidenceId,
    required this.onSelect,
  });

  final Idea idea;

  /// Выбранное доказательство. null — подсветки нет, график обычный.
  final String? selectedEvidenceId;

  final ValueChanged<String?> onSelect;

  @override
  Widget build(BuildContext context) {
    // Вклад в оценку — у компонента, доказательство — у детектора. Связывает
    // их `evidence_id` контракта §18: без этой связки строка тезиса и строка
    // разбора говорят об одном, но проверить это нельзя.
    final points = <String, ScoreContribution>{
      for (final c in idea.score.contributions)
        if (c.evidenceId != null) c.evidenceId!: c,
    };
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Почему идея появилась'),
          const SizedBox(height: 8),
          if (idea.thesis.isNotEmpty)
            Text(idea.thesis,
                style: T.body(12.5, color: C.textSecondary, height: 1.5)),
          if (idea.evidence.isEmpty)
            Text(
              'Расчёт не оставил разбора по факторам — показать нечего.',
              style: T.body(11.5, color: C.muted, height: 1.45),
            )
          else ...[
            const SizedBox(height: 11),
            for (var i = 0; i < idea.evidence.length; i++) ...[
              if (i > 0) const SizedBox(height: 7),
              _EvidenceRow(
                number: i + 1,
                evidence: idea.evidence[i],
                contribution: points[idea.evidence[i].id],
                selected: idea.evidence[i].id == selectedEvidenceId,
                // Доказательство без единой метки подсвечивать нечем:
                // строка остаётся, но не притворяется нажимаемой.
                onTap: idea.evidence[i].annotationIds.isEmpty
                    ? null
                    : () => onSelect(idea.evidence[i].id == selectedEvidenceId
                        ? null
                        : idea.evidence[i].id),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

class _EvidenceRow extends StatelessWidget {
  const _EvidenceRow({
    required this.number,
    required this.evidence,
    required this.contribution,
    required this.selected,
    required this.onTap,
  });

  /// Порядковый номер в тезисе (прототип: `evidence-icon`). Нумерация даёт
  /// доказательству имя, на которое можно сослаться словами.
  final int number;

  final Evidence evidence;

  /// Вклад этого доказательства в оценку. null — компонент не сослался на
  /// него, и придумывать баллы нельзя.
  final ScoreContribution? contribution;

  final bool selected;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final linked = onTap != null;
    return Pressable(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: selected ? C.accentFaint : C.inset,
          border: Border.all(color: selected ? C.accentBorder : C.divider),
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              width: 26,
              height: 26,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: selected ? C.accentFaint : C.chip,
                border: Border.all(color: selected ? C.accentBorder : C.border),
                borderRadius: BorderRadius.circular(R.chipLg),
              ),
              child: Text(
                '$number',
                style: T.mono(11, weight: 700, color: selected ? C.accent : C.muted),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    evidence.summary,
                    style: T.body(12,
                        weight: 700, color: selected ? C.accent : C.text),
                  ),
                  if (evidence.detail.isNotEmpty) ...[
                    const SizedBox(height: 3),
                    Text(evidence.detail,
                        style: T.body(11, color: C.muted, height: 1.4)),
                  ],
                ],
              ),
            ),
            if (contribution != null || linked) ...[
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  if (contribution != null)
                    Text(
                      contribution!.status == MeasurementStatus.measured
                          ? '${contribution!.points.round()}/'
                              '${(contribution!.effectiveWeight * 100).round()}'
                          : contribution!.status.label,
                      style: T.mono(11, weight: 600, color: C.textSecondary),
                    ),
                  if (linked)
                    Text(
                      selected ? 'на графике' : 'показать',
                      style: T.body(10, weight: 700, color: C.faint),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Условия, при которых замысел считается сломанным (ТЗ §9, зона Invalidation).
class InvalidationCard extends StatelessWidget {
  const InvalidationCard({super.key, required this.idea});

  final Idea idea;

  @override
  Widget build(BuildContext context) {
    final conditions = idea.plan?.invalidation ?? const <String>[];
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Когда идея сломана'),
          const SizedBox(height: 8),
          if (conditions.isEmpty)
            Text(
              'Жёсткие условия отмены не заданы: расчёт дал только стоп. '
              'Выход по стопу — единственное правило этой идеи.',
              style: T.body(11.5, color: C.muted, height: 1.45),
            )
          else
            for (final condition in conditions)
              Padding(
                padding: const EdgeInsets.only(bottom: 5),
                child: Text('· $condition',
                    style: T.body(12, height: 1.45)),
              ),
          if (idea.conflicting.isNotEmpty) ...[
            const SizedBox(height: 10),
            // ТЗ §9.1: конфликт детекторов показывается, а не прячется.
            for (final evidence in idea.conflicting)
              Container(
                margin: const EdgeInsets.only(bottom: 6),
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: C.warningFaint,
                  border: Border.all(color: C.warningBorder),
                  borderRadius: BorderRadius.circular(R.inset),
                ),
                child: Text(
                  'Расхождение: ${evidence.summary} против '
                  '${evidence.conflictsWith.join(", ")}',
                  style: T.body(11.5, color: C.warning, height: 1.4),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

/// Финальная проверка перед подтверждением (ТЗ §11.1).
///
/// Показываются все проверки сразу, а не первый отказ: иначе владелец чинит
/// препятствия по одному вслепую. Непроверенное отделено от проваленного —
/// «не знаю» и «нет» это разные ответы, хотя вход не даёт ни один.
class FinalCheckCard extends StatelessWidget {
  const FinalCheckCard({super.key, required this.results});

  final List<CheckResult> results;

  @override
  Widget build(BuildContext context) {
    final blockers = FinalCheck.blockers(results);
    final passed = results.length - blockers.length;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Проверка перед входом')),
              Text(
                '$passed из ${results.length}',
                style: T.mono(11, color: blockers.isEmpty ? C.green : C.red),
              ),
            ],
          ),
          const SizedBox(height: 10),
          for (final result in results) ...[
            _CheckLine(result: result),
            if (result != results.last) const SizedBox(height: 7),
          ],
        ],
      ),
    );
  }
}

class _CheckLine extends StatelessWidget {
  const _CheckLine({required this.result});

  final CheckResult result;

  @override
  Widget build(BuildContext context) {
    final color = result.passed
        ? C.green
        : result.skipped
            ? C.warning
            : C.red;
    // Знак рисуется вектором: символов ✓ и ✕ нет в подключённых шрифтах, и
    // на их месте выходил пустой квадрат — проверка выглядела сломанной.
    final mark = result.passed
        ? Icons.check
        : result.skipped
            ? Icons.unknown
            : Icons.cross;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 18,
          child: Padding(
            padding: const EdgeInsets.only(top: 1),
            child: VectorIcon(mark, size: 13, color: color),
          ),
        ),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(result.kind.label, style: T.body(12, weight: 700)),
              if (result.detail.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    result.detail,
                    style: T.body(11, color: result.passed ? C.muted : color, height: 1.4),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}
