import 'package:flutter/widgets.dart';

import '../../domain/idea/evidence.dart';
import '../../domain/idea/final_check.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/quality_score.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Из чего собрана оценка (ТЗ §14.2).
///
/// Показывает все восемь факторов, включая неизмеренные: оценка 60 из ста без
/// объяснения читается как «слабая идея», хотя на деле это может быть «мы не
/// посчитали два фактора из восьми». Разница принципиальная.
class ScoreBreakdownCard extends StatelessWidget {
  const ScoreBreakdownCard({super.key, required this.score});

  final QualityScore score;

  @override
  Widget build(BuildContext context) {
    final sorted = [...score.contributions]
      ..sort((a, b) => b.points.compareTo(a.points));
    return SectionCard(
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
            'Это оценка качества сетапа, а не вероятность прибыли.',
            style: T.body(11, color: C.faint, height: 1.4),
          ),
          const SizedBox(height: 10),
          for (final c in sorted) ...[
            _FactorLine(contribution: c),
            if (c != sorted.last) const SizedBox(height: 9),
          ],
          const SizedBox(height: 12),
          _riskLine(),
        ],
      ),
    );
  }

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

class _FactorLine extends StatelessWidget {
  const _FactorLine({required this.contribution});

  final FactorContribution contribution;

  @override
  Widget build(BuildContext context) {
    final measured = contribution.fraction > 0;
    final color = !measured
        ? C.faint
        : contribution.fraction >= 0.75
            ? C.green
            : contribution.fraction >= 0.4
                ? C.accent
                : C.warning;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(contribution.factor.label,
                  style: T.body(12, weight: 700)),
            ),
            Text(
              '${contribution.points.round()} из ${contribution.factor.weight}',
              style: T.mono(11, color: color),
            ),
          ],
        ),
        const SizedBox(height: 4),
        ClipRRect(
          borderRadius: BorderRadius.circular(R.pill),
          child: SizedBox(
            height: 5,
            child: Stack(
              children: [
                const ColoredBox(color: C.inset, child: SizedBox.expand()),
                FractionallySizedBox(
                  widthFactor: contribution.fraction.clamp(0.0, 1.0),
                  child:
                      ColoredBox(color: color, child: const SizedBox.expand()),
                ),
              ],
            ),
          ),
        ),
        if (contribution.note.isNotEmpty) ...[
          const SizedBox(height: 4),
          Text(contribution.note,
              style: T.body(11, color: C.muted, height: 1.4)),
        ],
      ],
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
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Почему эта идея есть'),
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
              const SizedBox(height: 10),
              for (final evidence in idea.evidence) ...[
                _EvidenceRow(
                  evidence: evidence,
                  selected: evidence.id == selectedEvidenceId,
                  // Доказательство без единой метки подсвечивать нечем:
                  // строка остаётся, но не притворяется нажимаемой.
                  onTap: evidence.annotationIds.isEmpty
                      ? null
                      : () => onSelect(
                          evidence.id == selectedEvidenceId ? null : evidence.id),
                ),
                if (evidence != idea.evidence.last) const SizedBox(height: 7),
              ],
            ],
          ],
        ),
      );
}

class _EvidenceRow extends StatelessWidget {
  const _EvidenceRow({
    required this.evidence,
    required this.selected,
    required this.onTap,
  });

  final Evidence evidence;
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
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(evidence.summary,
                      style: T.body(12,
                          weight: 700, color: selected ? C.accent : C.text)),
                ),
                if (linked)
                  Text(
                    selected ? 'на графике' : 'показать',
                    style: T.body(10, weight: 700, color: C.faint),
                  ),
              ],
            ),
            if (evidence.detail.isNotEmpty) ...[
              const SizedBox(height: 3),
              Text(evidence.detail,
                  style: T.body(11, color: C.muted, height: 1.4)),
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
    final mark = result.passed
        ? '✓'
        : result.skipped
            ? '?'
            : '✕';
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 16,
          child: Text(mark, style: T.mono(12, color: color)),
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
