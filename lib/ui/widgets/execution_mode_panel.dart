import 'package:flutter/widgets.dart';

import '../../state/execution_mode_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Owner-facing execution mode controls. Every write has a server preview and
/// a visible second confirmation; blocked previews never call a write endpoint.
class ExecutionModePanel extends StatelessWidget {
  const ExecutionModePanel({
    super.key,
    required this.controller,
  });

  final ExecutionModeController controller;

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: controller,
        builder: (context, _) {
          final preview = controller.preview;
          final live = controller.livePreview;
          final liveResult = controller.liveResult;
          return SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Режим исполнения'),
                const SizedBox(height: 7),
                Text(
                  'Текущий режим · ${controller.mode.label}',
                  style: T.body(12, weight: 750, color: C.text),
                ),
                const SizedBox(height: 5),
                Text(
                  'Режим хранится на сервере. Нажатие сначала только проверяет переход; '
                  'запись выполняется отдельным подтверждением.',
                  style: T.body(10.5, color: C.muted, height: 1.4),
                ),
                const SizedBox(height: 10),
                for (final target in const [
                  ServerExecutionMode.paper,
                  ServerExecutionMode.sandbox,
                  ServerExecutionMode.canary,
                  ServerExecutionMode.live,
                ]) ...[
                  ActionButton(
                    label: target.label,
                    dense: true,
                    color: target == ServerExecutionMode.live ? C.red : C.info,
                    onTap: target == controller.mode
                        ? null
                        : () => _preview(target),
                  ),
                  if (target != ServerExecutionMode.live) const SizedBox(height: 6),
                ],
                if (controller.error != null) ...[
                  const SizedBox(height: 9),
                  Text(
                    controller.error!,
                    style: T.body(10.5, color: C.warning, height: 1.4),
                  ),
                ],
                if (preview != null) ...[
                  const SizedBox(height: 10),
                  _GenericPreview(
                    preview: preview,
                    onConfirm: preview.allowed
                        ? () => controller.confirmModeChange(
                              reason:
                                  'owner confirmed ${preview.target.label} from app',
                            )
                        : null,
                    onCancel: controller.clearPendingPreview,
                  ),
                ],
                if (live != null) ...[
                  const SizedBox(height: 10),
                  _LivePreview(
                    preview: live,
                    result: liveResult,
                    onConfirm: live.confirmable
                        ? () => controller.confirmLive()
                        : null,
                    onCancel: controller.clearPendingPreview,
                  ),
                ],
              ],
            ),
          );
        },
      );

  Future<void> _preview(ServerExecutionMode target) async {
    if (target == ServerExecutionMode.live) {
      await controller.previewLive();
    } else {
      await controller.previewMode(target);
    }
  }
}

class _GenericPreview extends StatelessWidget {
  const _GenericPreview({
    required this.preview,
    required this.onConfirm,
    required this.onCancel,
  });

  final ExecutionModePreview preview;
  final Future<void> Function()? onConfirm;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) => InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${preview.current.label} → ${preview.target.label}',
              style: T.mono(11, color: C.text),
            ),
            if (preview.blockers.isNotEmpty) ...[
              const SizedBox(height: 6),
              for (final blocker in preview.blockers)
                Text(
                  '• $blocker',
                  style: T.body(10.5, color: C.warning, height: 1.35),
                ),
            ],
            if (onConfirm != null) ...[
              const SizedBox(height: 8),
              ActionButton(
                label: 'Подтвердить ${preview.target.label}',
                primary: true,
                dense: true,
                onTap: () => onConfirm!(),
              ),
            ],
            const SizedBox(height: 6),
            ActionButton(label: 'Отмена', dense: true, onTap: onCancel),
          ],
        ),
      );
}

class _LivePreview extends StatelessWidget {
  const _LivePreview({
    required this.preview,
    required this.result,
    required this.onConfirm,
    required this.onCancel,
  });

  final LiveActivationPreview preview;
  final LiveActivationResult? result;
  final Future<LiveActivationResult> Function()? onConfirm;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) {
    final blockers = result?.blockers.isNotEmpty == true
        ? result!.blockers
        : preview.blockers;
    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('CANARY → LIVE', style: T.mono(11, color: C.red)),
          const SizedBox(height: 6),
          KeyValueRow(name: 'Venue', value: preview.venue),
          KeyValueRow(name: 'Account', value: preview.account),
          KeyValueRow(
            name: 'Capital',
            value: '${_formatCapital(preview.capitalRub)} ₽',
          ),
          for (final entry in preview.hardCaps.entries)
            KeyValueRow(name: entry.key, value: entry.value),
          if (blockers.isNotEmpty) ...[
            const SizedBox(height: 7),
            for (final blocker in blockers)
              Text(
                '• $blocker',
                style: T.body(10.5, color: C.warning, height: 1.35),
              ),
          ],
          if (result != null) ...[
            const SizedBox(height: 7),
            Text(
              'Результат подтверждения · ${result!.status}',
              style: T.body(
                10.5,
                weight: 750,
                color: result!.status == 'APPLIED' ? C.red : C.warning,
              ),
            ),
          ],
          if (onConfirm != null) ...[
            const SizedBox(height: 9),
            ActionButton(
              label: 'Подтвердить LIVE',
              primary: true,
              dense: true,
              color: C.red,
              onTap: () => onConfirm!(),
            ),
          ],
          const SizedBox(height: 6),
          ActionButton(label: 'Отмена', dense: true, onTap: onCancel),
        ],
      ),
    );
  }
}

String _formatCapital(String raw) {
  final value = DecimalLike.tryParse(raw);
  if (value == null) return raw;
  final whole = value.truncate().toString();
  final chars = whole.split('').reversed.toList();
  final grouped = <String>[];
  for (var index = 0; index < chars.length; index++) {
    if (index > 0 && index % 3 == 0) grouped.add(' ');
    grouped.add(chars[index]);
  }
  return grouped.reversed.join();
}

/// Avoid a money-formatting dependency for one read-only preview field.
abstract final class DecimalLike {
  static double? tryParse(String raw) => double.tryParse(raw.replaceAll(',', '.'));
}
