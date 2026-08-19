import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import 'common.dart';
import 'risk_on_panel.dart';

/// Idea-scoped launcher for the RISK ON two-step flow.
///
/// This decorator is mounted only while a concrete actionable server idea is
/// open. The control therefore cannot create a global/unscoped risk override.
class RiskOnDetailControl extends StatefulWidget {
  const RiskOnDetailControl({
    super.key,
    required this.ideaId,
    required this.child,
  });

  final String ideaId;
  final Widget child;

  @override
  State<RiskOnDetailControl> createState() => _RiskOnDetailControlState();
}

class _RiskOnDetailControlState extends State<RiskOnDetailControl> {
  bool _open = false;

  @override
  void didUpdateWidget(RiskOnDetailControl oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.ideaId != widget.ideaId) _open = false;
  }

  @override
  Widget build(BuildContext context) => Stack(
        children: [
          widget.child,
          if (!_open)
            Positioned(
              right: S.screen,
              bottom: 82,
              child: SizedBox(
                width: 118,
                child: ActionButton(
                  label: 'RISK ON',
                  primary: true,
                  dense: true,
                  color: C.warning,
                  onTap: () => setState(() => _open = true),
                ),
              ),
            ),
          if (_open)
            Positioned.fill(
              child: Stack(
                children: [
                  Positioned.fill(
                    child: GestureDetector(
                      onTap: () => setState(() => _open = false),
                      child: const ColoredBox(color: Color(0xB3000000)),
                    ),
                  ),
                  Center(
                    child: ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 520),
                      child: SingleChildScrollView(
                        padding: const EdgeInsets.all(S.screen),
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            RiskOnPanel(
                              key: ValueKey('risk-on-${widget.ideaId}'),
                              ideaId: widget.ideaId,
                            ),
                            const SizedBox(height: 8),
                            ActionButton(
                              label: 'Закрыть',
                              dense: true,
                              onTap: () => setState(() => _open = false),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
        ],
      );
}
