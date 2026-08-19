import 'package:flutter/widgets.dart';

import '../../state/execution_mode_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';

/// Persistent thin-client safety banner. An unavailable server mode is shown as
/// unknown; it is never silently rendered as PAPER.
class ExecutionModeBanner extends StatelessWidget {
  const ExecutionModeBanner({
    super.key,
    required this.controller,
    required this.onManage,
  });

  final ExecutionModeController controller;
  final VoidCallback onManage;

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: controller,
        builder: (context, _) {
          final mode = controller.mode;
          final unknown = !controller.modeKnown;
          final live = mode == ServerExecutionMode.live;
          final canary = mode == ServerExecutionMode.canary;
          final tone = unknown
              ? C.warning
              : live
                  ? C.red
                  : canary
                      ? C.warning
                      : C.info;
          final fill = unknown
              ? C.warningFaint
              : live
                  ? C.redFaint
                  : canary
                      ? C.warningFaint
                      : C.infoFaint;

          return Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(horizontal: S.screen, vertical: 7),
            decoration: BoxDecoration(
              color: fill,
              border: Border(bottom: BorderSide(color: tone.withValues(alpha: .28))),
            ),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        'РЕЖИМ · ${mode.label}',
                        style: T.body(10.5, weight: 800, color: tone),
                      ),
                      if (unknown)
                        Text(
                          controller.loading
                              ? 'читаем server-owned mode…'
                              : 'серверный режим недоступен — локальное значение не подставлено',
                          style: T.body(9.5, color: C.muted, height: 1.3),
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                GestureDetector(
                  onTap: onManage,
                  behavior: HitTestBehavior.opaque,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 5),
                    child: Text(
                      'Управление',
                      style: T.body(10.5, weight: 750, color: C.text),
                    ),
                  ),
                ),
              ],
            ),
          );
        },
      );
}
