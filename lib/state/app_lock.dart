/// Pure lifecycle policy for the local SignalAI app lock.
///
/// Authentication itself stays on the native Android side.  This class only
/// decides *when* the app must ask for it, making the background timeout and
/// BiometricPrompt lifecycle behaviour deterministic and unit-testable.
class AppLockState {
  AppLockState({required this.timeout});

  final Duration timeout;

  bool _locked = true;
  bool _authenticating = false;
  DateTime? _backgroundedAt;

  bool get locked => _locked;
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
    // A system biometric prompt can transiently move the Activity through
    // inactive/paused states.  That must never count as real background time.
    _backgroundedAt = null;
  }

  void authenticationFailed() {
    _authenticating = false;
    _locked = true;
  }

  /// A device without a supported local authenticator must not be bricked by
  /// the convenience app-lock layer.  Trade confirmation remains independently
  /// fail-closed in the execution path.
  void authenticationUnavailable() {
    _authenticating = false;
    _locked = false;
    _backgroundedAt = null;
  }

  void backgrounded(DateTime at) {
    if (_authenticating) return;
    _backgroundedAt ??= at.toUtc();
  }

  /// Returns true when resume changed state to locked and authentication is due.
  bool resume(DateTime at) {
    if (_authenticating) return false;
    final backgroundedAt = _backgroundedAt;
    _backgroundedAt = null;
    if (_locked) return shouldAuthenticate;
    if (backgroundedAt == null) return false;

    final elapsed = at.toUtc().difference(backgroundedAt);
    if (elapsed >= timeout) {
      _locked = true;
      return true;
    }
    return false;
  }
}
