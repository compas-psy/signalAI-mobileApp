import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Блок уровней сделки: ВХОД · СТОП · ЦЕЛИ и бейдж R:R.
///
/// Макет v2 §5.1: вместо строки «Вход 121,4 SL 118,9 TP …» — три колонки с
/// микро-подписями. Подпись отвечает на вопрос «что это за число», значение
/// набрано моноширинным и раскрашено по смыслу: вход жёлтый, стоп красный,
/// цели зелёные. Глазу не нужно читать текст, чтобы найти стоп.
class LevelStrip extends StatelessWidget {
  const LevelStrip({
    super.key,
    required this.entry,
    required this.stop,
    required this.targets,
    this.riskReward,
    this.targetsLabel = 'ЦЕЛИ',
  });

  final String entry;
  final String stop;

  /// Уже отформатированные цели: соединяются точкой-разделителем.
  final List<String> targets;

  /// Текст бейджа справа, например «2,3 R:R». Без него бейдж не рисуется.
  final String? riskReward;

  final String targetsLabel;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _column('ВХОД', entry, C.accent),
            const SizedBox(width: 14),
            _column('СТОП', stop, C.red),
            const SizedBox(width: 14),
            Expanded(
              child: _column(targetsLabel, targets.join(' · '), C.green),
            ),
            if (riskReward != null) ...[
              const SizedBox(width: 10),
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: RrBadge(riskReward!),
              ),
            ],
          ],
        ),
      );

  Widget _column(String label, String value, Color color) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, style: T.microLabel()),
          const SizedBox(height: 3),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: T.mono(11.5, weight: 600, color: color),
          ),
        ],
      );
}

/// Бейдж соотношения риск/прибыль: жёлтый на полупрозрачной заливке.
class RrBadge extends StatelessWidget {
  const RrBadge(this.label, {super.key});

  final String label;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: C.accentFaint,
          border: Border.all(color: C.accentBorder),
          borderRadius: BorderRadius.circular(R.chip),
        ),
        child: Text(label, style: T.mono(10.5, weight: 600, color: C.accent)),
      );
}
