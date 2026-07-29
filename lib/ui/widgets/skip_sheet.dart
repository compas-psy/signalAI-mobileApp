import 'package:flutter/widgets.dart';

import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Пропуск идеи с причиной (ТЗ §12, приложение A).
///
/// Причина выбирается из закрытого справочника: свободный текст рядом, но не
/// вместо кода. Иначе разрезы журнала по причинам посчитать нечем, а именно
/// они отвечают на вопрос «я теряю деньги, потому что не вижу сигналы вовремя
/// или потому что им не верю» — лечится это по-разному.
class SkipSheet extends StatefulWidget {
  const SkipSheet({
    super.key,
    required this.idea,
    required this.onSkip,
    required this.onClose,
  });

  final Idea idea;
  final void Function(SkipReason reason, String comment) onSkip;
  final VoidCallback onClose;

  @override
  State<SkipSheet> createState() => _SkipSheetState();
}

class _SkipSheetState extends State<SkipSheet> {
  SkipReason? _reason;
  final _comment = TextEditingController();

  @override
  void dispose() {
    _comment.dispose();
    super.dispose();
  }

  /// «Другое» без пояснения ничего не сообщает и портит разрезы.
  bool get _ready {
    final reason = _reason;
    if (reason == null) return false;
    return !reason.needsComment || _comment.text.trim().isNotEmpty;
  }

  @override
  Widget build(BuildContext context) => Align(
        alignment: Alignment.bottomCenter,
        child: Container(
          padding: const EdgeInsets.fromLTRB(S.screen, 14, S.screen, 18),
          decoration: const BoxDecoration(
            color: C.sheet,
            border: Border(top: BorderSide(color: C.border)),
            borderRadius: BorderRadius.vertical(top: Radius.circular(R.sheet)),
          ),
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Center(
                  child: Container(
                    width: 38,
                    height: 4,
                    decoration: BoxDecoration(
                      color: C.handle,
                      borderRadius: BorderRadius.circular(R.pill),
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                Text('Пропустить ${widget.idea.instrumentId}',
                    style: T.jost(18)),
                const SizedBox(height: 6),
                Text(
                  'Причина попадёт в журнал и в разрезы статистики. Записи о '
                  'решениях не редактируются.',
                  style: T.body(11.5, color: C.muted, height: 1.45),
                ),
                const SizedBox(height: 14),
                for (final reason in SkipReason.values) ...[
                  _ReasonRow(
                    reason: reason,
                    selected: reason == _reason,
                    onTap: () => setState(() => _reason = reason),
                  ),
                  if (reason != SkipReason.values.last) const SizedBox(height: 6),
                ],
                const SizedBox(height: 12),
                _CommentField(
                  controller: _comment,
                  required: _reason?.needsComment ?? false,
                  onChanged: () => setState(() {}),
                ),
                const SizedBox(height: 14),
                Row(
                  children: [
                    Expanded(
                      child: ActionButton(
                        label: 'Записать пропуск',
                        primary: true,
                        onTap: _ready
                            ? () => widget.onSkip(_reason!, _comment.text.trim())
                            : null,
                      ),
                    ),
                    const SizedBox(width: 9),
                    ActionButton(label: 'Отмена', onTap: widget.onClose),
                  ],
                ),
              ],
            ),
          ),
        ),
      );
}

class _ReasonRow extends StatelessWidget {
  const _ReasonRow({
    required this.reason,
    required this.selected,
    required this.onTap,
  });

  final SkipReason reason;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          alignment: Alignment.centerLeft,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: selected ? C.accentFaint : C.inset,
            border: Border.all(color: selected ? C.accentBorder : C.divider),
            borderRadius: BorderRadius.circular(R.inner),
          ),
          child: Row(
            children: [
              Expanded(
                child: Text(
                  reason.label,
                  style: T.body(13,
                      weight: selected ? 800 : 600,
                      color: selected ? C.accent : C.text),
                ),
              ),
              Text(reason.code, style: T.mono(9.5, color: C.faint)),
            ],
          ),
        ),
      );
}

class _CommentField extends StatefulWidget {
  const _CommentField({
    required this.controller,
    required this.required,
    required this.onChanged,
  });

  final TextEditingController controller;
  final bool required;
  final VoidCallback onChanged;

  @override
  State<_CommentField> createState() => _CommentFieldState();
}

class _CommentFieldState extends State<_CommentField> {
  // Узел живёт со своим полем: создавать его в build значит терять фокус на
  // каждой перерисовке — а перерисовка тут на каждый введённый символ.
  final _focus = FocusNode();

  @override
  void dispose() {
    _focus.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: C.inset,
          border: Border.all(
              color: widget.required ? C.accentBorder : C.divider),
          borderRadius: BorderRadius.circular(R.inner),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.required
                  ? 'Комментарий обязателен'
                  : 'Комментарий (не обязателен)',
              style: T.body(10,
                  weight: 700, color: widget.required ? C.accent : C.faint),
            ),
            const SizedBox(height: 6),
            EditableText(
              controller: widget.controller,
              focusNode: _focus,
              style: T.body(13),
              cursorColor: C.accent,
              backgroundCursorColor: C.dim,
              maxLines: 3,
              minLines: 1,
              onChanged: (_) => widget.onChanged(),
            ),
          ],
        ),
      );
}
