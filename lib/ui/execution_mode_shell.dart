import 'dart:async';

import 'package:flutter/widgets.dart';

import '../state/app_controller.dart';
import '../state/app_scope.dart';
import '../state/execution_mode_controller.dart';
import '../state/navigation.dart';
import 'app_lock_gate.dart';
import 'widgets/execution_mode_banner.dart';

/// Thin-client shell for the server-owned execution lifecycle mode.
///
/// It deliberately waits until AppController finishes restoring the engine
/// address and device token before constructing ApiClient inside the mode
/// controller. The underlying AppShell remains untouched, which keeps mode UI
/// orthogonal to navigation and trading screens.
class ExecutionModeShell extends StatefulWidget {
  const ExecutionModeShell({
    super.key,
    required this.child,
  });

  final Widget child;

  @override
  State<ExecutionModeShell> createState() => _ExecutionModeShellState();
}

class _ExecutionModeShellState extends State<ExecutionModeShell> {
  ExecutionModeController? _modeController;
  String? _engineBaseUrl;
  bool _syncScheduled = false;

  @override
  void dispose() {
    _modeController?.dispose();
    super.dispose();
  }

  void _scheduleModeSync(AppController app) {
    final endpoint = app.engineBaseUrl;
    if (_syncScheduled || _engineBaseUrl == endpoint && _modeController != null) {
      return;
    }

    final firstLoad = _modeController == null;
    final mode = _modeController ??= ExecutionModeController();
    _engineBaseUrl = endpoint;
    _syncScheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      unawaited(
        (firstLoad ? mode.load() : mode.reconnect()).whenComplete(() {
          if (mounted) setState(() => _syncScheduled = false);
        }),
      );
    });
  }

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    Widget content;
    if (!app.thinMode || app.isLoading || app.engineAuthIssue != null) {
      content = widget.child;
    } else {
      _scheduleModeSync(app);
      final mode = _modeController!;
      content = ExecutionModeScope(
        controller: mode,
        child: Column(
          children: [
            ExecutionModeBanner(
              controller: mode,
              onManage: () {
                app.goSection(AppSection.settings);
                app.goPill(0);
              },
            ),
            Expanded(child: widget.child),
          ],
        ),
      );
    }

    // The privacy gate wraps the banner as well as the app body: account mode,
    // balances and signal state must not flash on screen before authentication.
    return AppLockGate(child: content);
  }
}
