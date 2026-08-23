import 'dart:async';

import 'package:flutter/widgets.dart';

import '../state/app_controller.dart';
import '../state/app_scope.dart';
import '../state/execution_mode_controller.dart';
import '../state/navigation.dart';
import 'app_lock_gate.dart';
import 'widgets/execution_mode_banner.dart';

/// Places the server-mode banner below the system status area and consumes the
/// top inset exactly once before handing the remaining viewport to AppShell.
///
/// AppShell already owns its own SafeArea. Without removeTop, wrapping the
/// banner in SafeArea would fix the overlap but add the same top inset again to
/// the body; without the banner SafeArea, the banner itself sits under Samsung's
/// status icons. Keeping this layout explicit makes both contracts testable.
class ExecutionModeInsetLayout extends StatelessWidget {
  const ExecutionModeInsetLayout({
    super.key,
    required this.banner,
    required this.child,
  });

  final Widget banner;
  final Widget child;

  @override
  Widget build(BuildContext context) => Column(
        children: [
          SafeArea(
            left: false,
            right: false,
            bottom: false,
            child: banner,
          ),
          Expanded(
            child: MediaQuery.removePadding(
              context: context,
              removeTop: true,
              child: child,
            ),
          ),
        ],
      );
}

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
        child: ExecutionModeInsetLayout(
          banner: ExecutionModeBanner(
            controller: mode,
            onManage: () {
              app.goSection(AppSection.settings);
              app.goPill(0);
            },
          ),
          child: widget.child,
        ),
      );
    }

    // Device-local authentication is a production privacy boundary. Demo/local
    // modes are deterministic development fixtures and intentionally have no
    // Android platform authenticator, so wrapping them would hide the test UI.
    if (!app.thinMode) return content;

    // In production, the gate wraps the banner as well as the app body: account
    // mode, balances and signal state must not flash before authentication.
    return AppLockGate(child: content);
  }
}
