/// Pure lifecycle policy for the local SignalAI app lock.
///
/// Authentication itself stays on the native Android side. This class only
/// decides *when* the app must ask for it and *when* financial UI must be
/// obscured, making lifecycle behaviour deterministic and unit-testable.
class AppLockState {
  AppLockState({required this.timeout});

  final Duration timeout;

  bool _locked = true;
  bool _obscured = false;
  bool _authenticating = false;
  DateTime? _backgroundedAt;

  bool get locked => _locked;
  bool get obscured => _obscured;
  bool get shouldHideContent => _locked || _obscured;
  bool get shouldAuthenticate => _locked && !_authenticating;
  bool get authenticating => _authenticating;

  /// Reserve one authentication attempt. Returns false when another prompt is
  /// already running or the app is already unlocked.
  bool beginAuthentication() {
    if (!shouldAuthenticate) return false;
    _authenticating = true;
    return true;
  }

  void authenticationSucceeded() {
    _authenticating = false;
    _locked = false;
    _obscured = false;
    // A system biometric prompt can transiently move the Activity through
    // inactive/paused states. That must never count as real background time.
    _backgroundedAt = null;
  }

  void authenticationFailed() {
    _authenticating = false;
    _locked = true;
    _obscured = false;
  }

  /// A device without a supported local authenticator must not be bricked by
  /// the convenience app-lock layer. Trade confirmation remains independently
  /// fail-closed in the execution path.
  void authenticationUnavailable() {
    _authenticating = false;
    _locked = false;
    _obscured = false;
    _backgroundedAt = null;
  }

  void backgrounded(DateTime at) {
    if (_authenticating) return;
    _backgroundedAt ??= at.toUtc();
    // Hide sensitive UI immediately so Android's task-switcher snapshot does
    // not retain balances, positions or signals. This does not itself require
    // re-authentication when the owner returns before [timeout].
    _obscured = true;
  }

  /// Returns true when resume changed state to locked and authentication is due.
  bool resume(DateTime at) {
    if (_authenticating) return false;
    final backgroundedAt = _backgroundedAt;
    _backgroundedAt = null;
    _obscured = false;

    // A locked screen after an explicit cancel stays passive. BiometricPrompt
    // itself commonly causes a resume event; auto-prompting here would trap the
    // owner in a cancellation loop. Cold start has its own one-shot prompt.
    if (_locked && backgroundedAt == null) return false;
    if (backgroundedAt == null) return false;

    final elapsed = at.toUtc().difference(backgroundedAt);
    if (elapsed >= timeout) {
      _locked = true;
      return true;
    }
    return false;
  }
}
