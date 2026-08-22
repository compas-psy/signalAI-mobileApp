import 'dart:async';

import 'package:flutter/material.dart';

import '../data/native_bridge.dart';
import '../state/app_lock.dart';
import '../theme/tokens.dart';
import '../theme/typography.dart';

/// Keeps account and signal data out of view until the device owner unlocks it.
///
/// This is a local privacy gate, not trading authority. Order confirmation and
/// server execution gates remain independent and fail closed on their own.
class AppLockGate extends StatefulWidget {
  const AppLockGate({
    super.key,
    required this.child,
    this.backgroundTimeout = const Duration(minutes: 5),
  });

  final Widget child;
  final Duration backgroundTimeout;

  @override
  State<AppLockGate> createState() => _AppLockGateState();
}

class _AppLockGateState extends State<AppLockGate> with WidgetsBindingObserver {
  static const _bridge = NativeBridge();
  late final AppLockState _lock = AppLockState(timeout: widget.backgroundTimeout);

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) unawaited(_authenticate());
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    switch (state) {
      case AppLifecycleState.resumed:
        if (_lock.resume(DateTime.now())) {
          unawaited(_authenticate());
        } else if (mounted) {
          setState(() {});
        }
      case AppLifecycleState.paused:
      case AppLifecycleState.hidden:
      case AppLifecycleState.detached:
        _lock.backgrounded(DateTime.now());
        // Render the privacy cover immediately so the Android recent-apps
        // thumbnail cannot retain balances, positions or signal details.
        if (mounted) setState(() {});
      case AppLifecycleState.inactive:
        // BiometricPrompt itself can make the Activity inactive. It must not
        // start the five-minute timer or recursively open another prompt.
        break;
    }
  }

  Future<void> _authenticate() async {
    if (!_lock.beginAuthentication()) return;
    if (mounted) setState(() {});

    final available = await _bridge.biometricsAvailable();
    if (!mounted) return;
    if (!available) {
      _lock.authenticationUnavailable();
      setState(() {});
      return;
    }

    final ok = await _bridge.biometricConfirm(
      title: 'Открыть SignalAI',
      subtitle: 'Подтвердите, что это вы',
    );
    if (!mounted) return;
    if (ok) {
      _lock.authenticationSucceeded();
    } else {
      _lock.authenticationFailed();
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    if (!_lock.shouldHideContent) return widget.child;

    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(28),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.lock_outline_rounded, size: 34, color: C.accent),
                const SizedBox(height: 16),
                Text('SignalAI заблокирован', style: T.jost(20, weight: 700)),
                const SizedBox(height: 7),
                Text(
                  'Финансовые данные скрыты. Для входа используйте отпечаток, лицо или код блокировки телефона.',
                  textAlign: TextAlign.center,
                  style: T.body(12, color: C.muted, height: 1.45),
                ),
                if (!_lock.obscured) ...[
                  const SizedBox(height: 18),
                  FilledButton(
                    onPressed: _lock.shouldAuthenticate ? () => unawaited(_authenticate()) : null,
                    child: Text(_lock.authenticating ? 'Проверяю…' : 'Разблокировать'),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
