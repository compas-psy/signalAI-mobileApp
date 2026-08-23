import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/device_enrollment.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/native_bridge.dart';

class _Store extends LocalStore {
  Map<String, dynamic>? document;

  @override
  Future<Map<String, dynamic>?> read(String name) async => document;

  @override
  Future<void> write(String name, Map<String, dynamic> value) async {
    document = Map<String, dynamic>.from(value);
  }

  @override
  Future<bool> writeDurably(String name, Map<String, dynamic> value) async {
    document = Map<String, dynamic>.from(value);
    return true;
  }
}

class _OwnerBridge extends NativeBridge {
  _OwnerBridge({this.publicKey});

  final String? publicKey;
  String? storedToken;
  bool ownerKeyDeleted = false;

  @override
  Future<bool> vaultAvailable() async => true;

  @override
  Future<String?> appVersion() async => null;

  @override
  Future<String?> ownerStepUpPublicKey() async => publicKey;

  @override
  Future<bool> putEngineDeviceToken(String token) async {
    storedToken = token;
    return true;
  }

  @override
  Future<String?> engineDeviceToken() async => storedToken;

  @override
  Future<bool> deleteEngineDeviceToken() async {
    storedToken = null;
    return true;
  }

  @override
  Future<bool> deleteOwnerStepUpKey() async {
    ownerKeyDeleted = true;
    return true;
  }
}

class _Api implements DeviceEnrollmentApi {
  String? ownerPublicKey;
  String? pairedDeviceId;
  String? revocationBearer;

  @override
  Future<DeviceEnrollmentReceipt> pair({
    required String baseUrl,
    required String bootstrapToken,
    required String pairingSessionId,
    required String deviceId,
    required Map<String, String> metadata,
    required String idempotencyKey,
    String? ownerPublicKeySpkiB64,
  }) async {
    ownerPublicKey = ownerPublicKeySpkiB64;
    pairedDeviceId = deviceId;
    return DeviceEnrollmentReceipt(
      deviceId: deviceId,
      generation: 1,
      deviceToken: 'iIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIi',
    );
  }

  @override
  Future<DeviceEnrollmentReceipt> rotate({
    required String baseUrl,
    required String activeDeviceToken,
    required String idempotencyKey,
  }) async =>
      DeviceEnrollmentReceipt(
        deviceId: pairedDeviceId ?? 'owner-step-up-device-0001',
        generation: 2,
        deviceToken: 'rRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRr',
      );

  @override
  Future<DeviceRevocationOutcome> revoke({
    required String baseUrl,
    required String activeDeviceToken,
  }) async {
    revocationBearer = activeDeviceToken;
    return DeviceRevocationOutcome.revoked;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('pairing forwards only the hardware owner public key to the server', () async {
    const publicKey = 'MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEowner-public-key-only';
    final bridge = _OwnerBridge(publicKey: publicKey);
    final api = _Api();
    final store = _Store();

    await pairAndStoreEngineDevice(
      store,
      bridge,
      baseUrl: 'https://engine.example.ru',
      bootstrapToken: 'bootstrap-secret',
      pairingSessionId: List<String>.filled(43, 's').join(),
      api: api,
    );

    expect(api.ownerPublicKey, publicKey);
    expect(store.document.toString(), isNot(contains(publicKey)));
    expect(store.document.toString(), isNot(contains('owner-private')));
  });

  test('pairing remains non-live-capable when strong biometric key is unavailable',
      () async {
    final bridge = _OwnerBridge(publicKey: null);
    final api = _Api();

    await pairAndStoreEngineDevice(
      _Store(),
      bridge,
      baseUrl: 'https://engine.example.ru',
      bootstrapToken: 'bootstrap-secret',
      pairingSessionId: List<String>.filled(43, 's').join(),
      api: api,
    );

    expect(api.ownerPublicKey, isNull);
  });

  test('confirmed remote forget deletes bearer and owner signing key', () async {
    final bridge = _OwnerBridge(publicKey: 'public')
      ..storedToken = 'fFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFf';
    final store = _Store()
      ..document = {
        'base_url': 'https://engine.example.ru',
        'device_id': 'owner-step-up-device-0002',
        'device_enrollment_v1': true,
      };

    await forgetEngineDevice(
      store,
      bridge,
      baseUrl: 'https://engine.example.ru',
      api: _Api(),
    );

    expect(bridge.storedToken, isNull);
    expect(bridge.ownerKeyDeleted, isTrue);
  });

  test('Android signer is per-use BIOMETRIC_STRONG and private key never crosses channel',
      () async {
    final signer = File(
      'android/app/src/main/kotlin/ru/signalai/app/OwnerStepUpSigner.kt',
    ).readAsStringSync();
    final activity = File(
      'android/app/src/main/kotlin/ru/signalai/app/MainActivity.kt',
    ).readAsStringSync();

    expect(signer, contains('KeyProperties.KEY_ALGORITHM_EC'));
    expect(signer, contains('KeyProperties.PURPOSE_SIGN'));
    expect(signer, contains('KeyProperties.DIGEST_SHA256'));
    expect(signer, contains('setUserAuthenticationRequired(true)'));
    expect(
      signer,
      contains(
        'setUserAuthenticationParameters(0, KeyProperties.AUTH_BIOMETRIC_STRONG)',
      ),
    );
    expect(signer, contains('setInvalidatedByBiometricEnrollment(true)'));
    expect(signer, contains('setIsStrongBoxBacked(true)'));
    expect(signer, contains('BiometricPrompt.CryptoObject(signature)'));
    expect(signer, isNot(contains('DEVICE_CREDENTIAL')));
    expect(signer, isNot(contains('privateKey.encoded')));
    expect(activity, contains('"ownerStepUpPublicKey"'));
    expect(activity, contains('"ownerStepUpSign"'));
    expect(activity, contains('ownerStepUpSigner'));
  });
}
