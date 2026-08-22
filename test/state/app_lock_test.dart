import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/state/app_lock.dart';

void main() {
  group('AppLockState', () {
    test('cold start is locked until local authentication succeeds', () {
      final lock = AppLockState(timeout: const Duration(minutes: 5));

      expect(lock.locked, isTrue);
      expect(lock.shouldHideContent, isTrue);
      expect(lock.shouldAuthenticate, isTrue);

      lock.authenticationSucceeded();
      expect(lock.locked, isFalse);
      expect(lock.obscured, isFalse);
      expect(lock.shouldHideContent, isFalse);
      expect(lock.shouldAuthenticate, isFalse);
    });

    test('background hides content immediately but short trip needs no auth', () {
      final lock = AppLockState(timeout: const Duration(minutes: 5));
      final t0 = DateTime.utc(2026, 8, 22, 7, 0);
      lock.authenticationSucceeded();
      lock.backgrounded(t0);

      expect(lock.locked, isFalse);
      expect(lock.obscured, isTrue);
      expect(lock.shouldHideContent, isTrue);
      expect(lock.shouldAuthenticate, isFalse);

      expect(lock.resume(t0.add(const Duration(minutes: 4, seconds: 59))), isFalse);
      expect(lock.locked, isFalse);
      expect(lock.obscured, isFalse);
      expect(lock.shouldHideContent, isFalse);
      expect(lock.shouldAuthenticate, isFalse);
    });

    test('five minutes in background requires authentication again', () {
      final lock = AppLockState(timeout: const Duration(minutes: 5));
      final t0 = DateTime.utc(2026, 8, 22, 7, 0);
      lock.authenticationSucceeded();
      lock.backgrounded(t0);

      expect(lock.obscured, isTrue);
      expect(lock.resume(t0.add(const Duration(minutes: 5))), isTrue);
      expect(lock.obscured, isFalse);
      expect(lock.locked, isTrue);
      expect(lock.shouldHideContent, isTrue);
      expect(lock.shouldAuthenticate, isTrue);
    });

    test('biometric prompt lifecycle does not hide or relock itself', () {
      final lock = AppLockState(timeout: const Duration(minutes: 5));
      final t0 = DateTime.utc(2026, 8, 22, 7, 0);

      expect(lock.beginAuthentication(), isTrue);
      lock.backgrounded(t0);
      expect(lock.obscured, isFalse);
      lock.authenticationSucceeded();

      expect(lock.resume(t0.add(const Duration(minutes: 30))), isFalse);
      expect(lock.locked, isFalse);
      expect(lock.obscured, isFalse);
      expect(lock.shouldHideContent, isFalse);
    });

    test('cancelled authentication stays on lock screen without prompt loop', () {
      final lock = AppLockState(timeout: const Duration(minutes: 5));
      final t0 = DateTime.utc(2026, 8, 22, 7, 0);

      expect(lock.beginAuthentication(), isTrue);
      lock.authenticationFailed();
      expect(lock.locked, isTrue);
      expect(lock.obscured, isFalse);
      expect(lock.shouldHideContent, isTrue);
      expect(lock.shouldAuthenticate, isTrue);

      // BiometricPrompt commonly generates an Activity resume after its own
      // cancellation. That lifecycle event must not immediately prompt again.
      expect(lock.resume(t0), isFalse);
      expect(lock.locked, isTrue);
      expect(lock.shouldAuthenticate, isTrue);

      // The owner can retry explicitly from the lock screen.
      expect(lock.beginAuthentication(), isTrue);
    });

    test('no local authenticator clears both lock and privacy cover', () {
      final lock = AppLockState(timeout: const Duration(minutes: 5));
      final t0 = DateTime.utc(2026, 8, 22, 7, 0);
      lock.authenticationSucceeded();
      lock.backgrounded(t0);
      expect(lock.shouldHideContent, isTrue);

      lock.authenticationUnavailable();
      expect(lock.locked, isFalse);
      expect(lock.obscured, isFalse);
      expect(lock.shouldHideContent, isFalse);
      expect(lock.shouldAuthenticate, isFalse);
    });
  });
}
