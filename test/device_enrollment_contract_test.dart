import 'dart:math';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/device_enrollment.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/native_bridge.dart';

final _pairingSession = List<String>.filled(43, 's').join();

class _DurableStore extends LocalStore {
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

class _Vault extends NativeBridge {
  String? token;
  bool deleteSucceeds = true;

  @override
  Future<bool> vaultAvailable() async => true;

  @override
  Future<bool> putEngineDeviceToken(String value) async {
    token = value;
    return true;
  }

  @override
  Future<String?> engineDeviceToken() async => token;

  @override
  Future<bool> deleteEngineDeviceToken() async {
    if (!deleteSucceeds) return false;
    token = null;
    return true;
  }
}

class _PairingApi implements DeviceEnrollmentApi {
  String? bootstrap;
  String? pairingSession;
  String? deviceId;
  String? idempotencyKey;
  String? rotationBearer;
  String? rotationIdempotencyKey;
  String? revocationBearer;
  Map<String, String>? metadata;
  final String issuedToken;
  final String rotatedToken =
      'rRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRrRr';
  DeviceRevocationOutcome revocationOutcome;
  bool revokeFails = false;

  _PairingApi({
    this.issuedToken = 'iIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIi',
    this.revocationOutcome = DeviceRevocationOutcome.revoked,
  });

  @override
  Future<DeviceEnrollmentReceipt> pair({
    required String baseUrl,
    required String bootstrapToken,
    required String pairingSessionId,
    required String deviceId,
    required Map<String, String> metadata,
    required String idempotencyKey,
  }) async {
    bootstrap = bootstrapToken;
    pairingSession = pairingSessionId;
    this.deviceId = deviceId;
    this.idempotencyKey = idempotencyKey;
    this.metadata = metadata;
    return DeviceEnrollmentReceipt(
      deviceId: deviceId,
      generation: 1,
      deviceToken: issuedToken,
    );
  }

  @override
  Future<DeviceEnrollmentReceipt> rotate({
    required String baseUrl,
    required String activeDeviceToken,
    required String idempotencyKey,
  }) async {
    rotationBearer = activeDeviceToken;
    rotationIdempotencyKey = idempotencyKey;
    return DeviceEnrollmentReceipt(
      deviceId: deviceId ?? 'android-rotated-device-01',
      generation: 2,
      deviceToken: rotatedToken,
    );
  }

  @override
  Future<DeviceRevocationOutcome> revoke({
    required String baseUrl,
    required String activeDeviceToken,
  }) async {
    revocationBearer = activeDeviceToken;
    if (revokeFails) {
      throw const DeviceEnrollmentException('Сервер недоступен.');
    }
    return revocationOutcome;
  }
}

class _InterruptedPairingApi implements DeviceEnrollmentApi {
  @override
  Future<DeviceEnrollmentReceipt> pair({
    required String baseUrl,
    required String bootstrapToken,
    required String pairingSessionId,
    required String deviceId,
    required Map<String, String> metadata,
    required String idempotencyKey,
  }) async =>
      throw const DeviceEnrollmentException('Сеть прервала pairing.');

  @override
  Future<DeviceEnrollmentReceipt> rotate({
    required String baseUrl,
    required String activeDeviceToken,
    required String idempotencyKey,
  }) async =>
      throw const DeviceEnrollmentException('Сеть прервала rotation.');

  @override
  Future<DeviceRevocationOutcome> revoke({
    required String baseUrl,
    required String activeDeviceToken,
  }) async =>
      throw const DeviceEnrollmentException('Сеть прервала revoke.');
}

void main() {
  test('bootstrap is exchanged once and only issued device token enters Keystore',
      () async {
    final store = _DurableStore();
    final vault = _Vault();
    final api = _PairingApi();
    const bootstrap = 'bootstrap-secret-that-must-not-be-stored';
    final pairingSession = _pairingSession;

    final receipt = await pairAndStoreEngineDevice(
      store,
      vault,
      baseUrl: 'https://engine.example.ru',
      bootstrapToken: bootstrap,
      pairingSessionId: pairingSession,
      api: api,
      random: Random(7),
    );

    expect(api.bootstrap, bootstrap);
    expect(api.pairingSession, pairingSession);
    expect(api.deviceId, receipt.deviceId);
    expect(api.idempotencyKey, hasLength(greaterThanOrEqualTo(16)));
    expect(api.metadata, {
      'label': 'SignalAI device',
      'platform': 'android',
    });
    expect(vault.token, receipt.deviceToken);
    expect(receipt.deviceToken, isNot(bootstrap));
    expect(store.document, {
      'base_url': 'https://engine.example.ru',
      'device_id': receipt.deviceId,
      'device_enrollment_v1': true,
    });
    expect(store.document.toString(), isNot(contains(bootstrap)));
    expect(store.document.toString(), isNot(contains(pairingSession)));
    expect(store.document.toString(), isNot(contains(receipt.deviceToken)));
  });

  test('malformed issued token never replaces an existing secure credential',
      () async {
    final store = _DurableStore();
    final vault = _Vault();
    final api = _PairingApi(issuedToken: 'too-short');

    await expectLater(
      pairAndStoreEngineDevice(
        store,
        vault,
        baseUrl: 'https://engine.example.ru',
        bootstrapToken: 'bootstrap-secret',
        pairingSessionId: _pairingSession,
        api: api,
        random: Random(1),
      ),
      throwsA(isA<DeviceEnrollmentException>()),
    );
    expect(vault.token, isNull);
    expect(store.document.toString(), isNot(contains('bootstrap-secret')));
  });

  test('interrupted pairing retains the same durable idempotency key', () async {
    final store = _DurableStore();
    final vault = _Vault();

    await expectLater(
      pairAndStoreEngineDevice(
        store,
        vault,
        baseUrl: 'https://engine.example.ru',
        bootstrapToken: 'bootstrap-secret',
        pairingSessionId: _pairingSession,
        api: _InterruptedPairingApi(),
        random: Random(4),
      ),
      throwsA(isA<DeviceEnrollmentException>()),
    );
    final retainedKey = store.document!['pairing_request_id'];
    final successfulApi = _PairingApi();
    await pairAndStoreEngineDevice(
      store,
      vault,
      baseUrl: 'https://engine.example.ru',
      bootstrapToken: 'bootstrap-secret',
      pairingSessionId: _pairingSession,
      api: successfulApi,
      random: Random(99),
    );

    expect(successfulApi.idempotencyKey, retainedKey);
  });

  test('rotation replaces the Keystore bearer only after server issuance', () async {
    final store = _DurableStore()
      ..document = {
        'base_url': 'https://engine.example.ru',
        'device_id': 'android-rotated-device-01',
        'device_enrollment_v1': true,
      };
    final vault = _Vault()..token = 'oOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOo';
    final api = _PairingApi();

    final receipt = await rotateAndStoreEngineDevice(
      store,
      vault,
      baseUrl: 'https://engine.example.ru',
      api: api,
      random: Random(9),
    );

    expect(api.rotationBearer, 'oOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOoOo');
    expect(api.rotationIdempotencyKey, hasLength(greaterThanOrEqualTo(16)));
    expect(vault.token, receipt.deviceToken);
    expect(vault.token, isNot(api.rotationBearer));
    expect(store.document.toString(), isNot(contains(api.rotationBearer!)));
  });

  test('forget revokes remotely before deleting the local device bearer', () async {
    final store = _DurableStore()
      ..document = {
        'base_url': 'https://engine.example.ru',
        'device_id': 'android-forget-device-01',
        'device_enrollment_v1': true,
      };
    final vault = _Vault()..token = 'fFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFf';
    final api = _PairingApi(
      revocationOutcome: DeviceRevocationOutcome.alreadyRevoked,
    );

    await forgetEngineDevice(
      store,
      vault,
      baseUrl: 'https://engine.example.ru',
      api: api,
    );

    expect(api.revocationBearer, 'fFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFfFf');
    expect(vault.token, isNull);
    expect(store.document, {'base_url': 'https://engine.example.ru'});
  });

  test('forget retains local state when remote revocation is not confirmed', () async {
    final store = _DurableStore()
      ..document = {
        'base_url': 'https://engine.example.ru',
        'device_id': 'android-forget-device-02',
        'device_enrollment_v1': true,
      };
    final vault = _Vault()..token = 'gGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGg';
    final api = _PairingApi()..revokeFails = true;

    await expectLater(
      forgetEngineDevice(
        store,
        vault,
        baseUrl: 'https://engine.example.ru',
        api: api,
      ),
      throwsA(isA<DeviceEnrollmentException>()),
    );
    expect(vault.token, 'gGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgGg');
    expect(store.document!['device_enrollment_v1'], isTrue);
  });
}
