import 'package:flutter/widgets.dart';

import '../../domain/idea/execution.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'execution_timeline_card.dart';
import 'manual_trade_controls.dart';

/// Состояние исполнения плана на экране (ТЗ §11.3, §21).
///
/// Отвечает на три вопроса, которые возникают, когда деньги уже в рынке: где
/// сейчас заявка, стоит ли защита и что движок делает дальше. До этой полосы
/// подтверждение заканчивалось тостом, и дальше владелец не знал ничего.
class ExecutionStrip extends StatelessWidget {
  const ExecutionStrip({
    super.key,
    required this.execution,
    required this.now,
  });

  final Execution execution;
  final DateTime now;

  /// Позиция открыта, защиты нет и срок вышел — это авария, а не состояние.
  bool get _emergency => ExecutionRules.needsCompensatingClose(execution, now);

  Color get _tone {
    if (_emergency || execution.state == ExecutionState.safeState) return C.red;
    if (execution.state.isNaked) return C.warning;
    if (execution.state == ExecutionState.closed) return C.muted;
    return C.green;
  }

  @override
  Widget build(BuildContext context) {
    final tone = _tone;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Исполнение')),
              OutlineBadge(
                label: execution.state.label,
                color: tone,
                borderColor: tone.withValues(alpha: 0.35),
                background: tone.withValues(alpha: 0.12),
                fontWeight: 800,
              ),
            ],
          ),
          const SizedBox(height: 8),
          _Line(
            label: 'Объём',
            value: '${execution.filledQuantity} из '
                '${execution.plannedQuantity}',
            hint: execution.remainingQuantity > 0
                ? 'не набрано ${execution.remainingQuantity}'
                : null,
          ),
          const SizedBox(height: 6),
          _Line(
            label: 'Защита',
            value: execution.protection.label,
            // Пока защита не встала, объём стопа считается по набранному:
            // стоп на плановый объём при частичном входе — это шорт.
            hint: execution.state.hasPosition
                ? 'на ${execution.protectiveQuantity} ед.'
                : null,
            tone: execution.state.isNaked ? C.warning : null,
          ),
          if (execution.averageEntry != null) ...[
            const SizedBox(height: 6),
            _Line(
              label: 'Средний вход',
              value: execution.averageEntry!
                  .toStringAsFixed(2)
                  .replaceAll('.', ','),
            ),
          ],
          const SizedBox(height: 10),
          Container(
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: _emergency ? C.redFaint : C.inset,
              border: _emergency ? Border.all(color: C.redBorder) : null,
              borderRadius: BorderRadius.circular(R.inset),
            ),
            child: Text(
              _emergency
                  ? 'Позиция открыта без защиты дольше '
                      '${ExecutionRules.protectionSla.inSeconds} секунд. '
                      'Нужно закрыть её вручную или выставить стоп — '
                      'дальше риск ничем не ограничен.'
                  : ExecutionRules.actionOn(execution.state),
              style: T.body(
                11.5,
                color: _emergency ? C.red : C.muted,
                height: 1.45,
              ),
            ),
          ),
          if (execution.note.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              execution.note,
              style: T.body(11, color: C.faint, height: 1.4),
            ),
          ],
          if (execution.state.isProtected) ...[
            const SizedBox(height: 14),
            ManualTradeControls(ideaId: execution.ideaId),
          ],
          const SizedBox(height: 14),
          ExecutionTimelineCard(ideaId: execution.ideaId),
        ],
      ),
    );
  }
}

class _Line extends StatelessWidget {
  const _Line({
    required this.label,
    required this.value,
    this.hint,
    this.tone,
  });

  final String label;
  final String value;
  final String? hint;
  final Color? tone;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Expanded(child: Text(label, style: T.body(12, color: C.muted))),
          if (hint != null) ...[
            Text(hint!, style: T.body(10.5, color: C.faint)),
            const SizedBox(width: 8),
          ],
          Text(value, style: T.mono(12, color: tone ?? C.text)),
        ],
      );
}
