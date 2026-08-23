import 'dart:convert';
import 'dart:math';

import '../local_store.dart';
import '../native_bridge.dart';
import 'api_client.dart';

/// Sanitised failure for the pairing UI.  It never embeds a bearer token.
class DeviceEnrollmentException implements Exception {
  const DeviceEnrollmentException(this.message);

  final String message;

  @override
  String toString() => message;
}

class DeviceEnrollmentReceipt {
  const DeviceEnrollmentReceipt({
    required this.deviceId,
    required this.generation,
    required this.deviceToken,
  });

  final String deviceId;
  final int generation;
  final String deviceToken;
}

/// The self-revocation endpoint is deliberately idempotent.  A phone may
/// erase the Keystore only after one of these explicit server outcomes.
enum DeviceRevocationOutcome { revoked, alreadyRevoked }

/// Narrow transport boundary that keeps tests independent of a real socket.
abstract interface class DeviceEnrollmentApi {
  Future<DeviceEnrollmentReceipt> pair({
    required String baseUrl,
    required String bootstrapToken,
    required String pairingSessionId,
    required String deviceId,
    required Map<String, String> metadata,
    required String idempotencyKey,
    String? ownerPublicKeySpkiB64,
  });

  Future<DeviceEnrollmentReceipt> rotate({
    required String baseUrl,
    required String activeDeviceToken,
    required String idempotencyKey,
  });

  Future<DeviceRevocationOutcome> revoke({
    required String baseUrl,
    required String activeDeviceToken,
  });
}

class HttpDeviceEnrollmentApi implements DeviceEnrollmentApi {
  const HttpDeviceEnrollmentApi();

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
    final client = ApiClient(baseUrl: baseUrl, deviceToken: bootstrapToken);
    try {
      final requestBody = <String, dynamic>{
        'device_id': deviceId,
        'metadata': metadata,
      };
      if (ownerPublicKeySpkiB64 != null) {
        requestBody['owner_public_key_spki_b64'] = ownerPublicKeySpkiB64;
      }
      final body = await client.postForPairing(
        '/api/v1/device-enrollment/pair',
        body: requestBody,
        idempotencyKey: idempotencyKey,
        pairingSessionId: pairingSessionId,
      );
      return _receipt(body, expectedDeviceId: deviceId);
    } on DeviceEnrollmentException {
      rethrow;
    } on ApiException catch (error) {
      // ApiException contains a user-facing HTTP error, not request headers.
      throw DeviceEnrollmentException(error.message);
    } finally {
      client.close();
    }
  }

  @override
  Future<DeviceEnrollmentReceipt> rotate({
    required String baseUrl,
    required String activeDeviceToken,
    required String idempotencyKey,
  }) async {
    final client = ApiClient(baseUrl: baseUrl, deviceToken: activeDeviceToken);
    try {
      final body = await client.post(
        '/api/v1/device-enrollment/rotate',
        idempotencyKey: idempotencyKey,
      );
      return _receipt(body);
    } on DeviceEnrollmentException {
      rethrow;
    } on ApiException catch (error) {
      throw DeviceEnrollmentException(error.message);
    } finally {
      client.close();
    }
  }

  @override
  Future<DeviceRevocationOutcome> revoke({
    required String baseUrl,
    required String activeDeviceToken,
  }) async {
    final client = ApiClient(baseUrl: baseUrl, deviceToken: activeDeviceToken);
    try {
      final body = await client.post('/api/v1/device-enrollment/revoke');
      return switch (body['status']) {
        'revoked' => DeviceRevocationOutcome.revoked,
        'already_revoked' => DeviceRevocationOutcome.alreadyRevoked,
        _ => throw const DeviceEnrollmentException('Сервер не подтвердил отзыв устройства.'),
      };
    } on DeviceEnrollmentException {
      rethrow;
    } on ApiException catch (error) {
      throw DeviceEnrollmentException(error.message);
    } finally {
      client.close();
    }
  }
}

bool _isDeviceToken(Object? value) =>
    value is String && RegExp(r'^[A-Za-z0-9_-]{43,128}$').hasMatch(value);

bool _isDeviceId(Object? value) =>
    value is String && RegExp(r'^[A-Za-z0-9_-]{16,64}$').hasMatch(value);

DeviceEnrollmentReceipt _receipt(
  Map<String, dynamic> body, {
  String? expectedDeviceId,
}) {
  final returnedId = body['device_id'];
  final generation = body['generation'];
  final token = body['device_token'];
  if (!_isDeviceId(returnedId) ||
      (expectedDeviceId != null && returnedId != expectedDeviceId) ||
      generation is! int ||
      generation < 1 ||
      !_isDeviceToken(token)) {
    throw const DeviceEnrollmentException('Сервер вернул некорректную привязку.');
  }
  return DeviceEnrollmentReceipt(
    deviceId: returnedId as String,
    generation: generation,
    deviceToken: token as String,
  );
}

String _randomUrlSafe(Random random) => base64UrlEncode(
      List<int>.generate(24, (_) => random.nextInt(256)),
    ).replaceAll('=', '');

/// Pair once with a bootstrap secret and atomically replace it with the issued
/// active-device token in the existing Android Keystore.
///
/// The local document deliberately contains no bearer or owner public key.
/// The hardware owner key is generated/reused natively and only its SPKI public
/// key is sent inside the owner-provisioned pairing capability.  If strong
/// biometrics are unavailable pairing still works, but owner step-up cannot.
Future<DeviceEnrollmentReceipt> pairAndStoreEngineDevice(
  LocalStore store,
  NativeBridge bridge, {
  required String baseUrl,
  required String bootstrapToken,
  required String pairingSessionId,
  DeviceEnrollmentApi api = const HttpDeviceEnrollmentApi(),
  Random? random,
}) async {
  if (!baseUrl.startsWith('https://')) {
    throw const DeviceEnrollmentException('Адрес движка должен использовать HTTPS.');
  }
  if (!_isDeviceToken(pairingSessionId)) {
    throw const DeviceEnrollmentException('Нужен действующий токен сессии привязки.');
  }
  if (!await bridge.vaultAvailable()) {
    throw const DeviceEnrollmentException(
      'Устройство не привязано: Android Keystore недоступен.',
    );
  }

  final ownerPublicKey = await bridge.ownerStepUpPublicKey();
  final source = random ?? Random.secure();
  final saved = await store.read('engine') ?? <String, dynamic>{};
  final storedId = saved['device_id'];
  final deviceId = storedId is String && RegExp(r'^[A-Za-z0-9_-]{16,64}$').hasMatch(storedId)
      ? storedId
      : _randomUrlSafe(source);
  final storedRequest = saved['base_url'] == baseUrl
      ? saved['pairing_request_id']
      : null;
  final idempotencyKey = storedRequest is String &&
          RegExp(r'^[A-Za-z0-9._:-]{16,128}$').hasMatch(storedRequest)
      ? storedRequest
      : _randomUrlSafe(source);
  final version = (await bridge.appVersion() ?? '').trim();
  final metadata = <String, String>{
    'label': 'SignalAI device',
    'platform': 'android',
    if (version.isNotEmpty && version.length <= 32) 'app_version': version,
  };

  final durableRequest = <String, dynamic>{
    'base_url': baseUrl,
    'device_id': deviceId,
    'device_enrollment_v1': true,
    'pairing_request_id': idempotencyKey,
  };
  if (!await store.writeDurably('engine', durableRequest)) {
    throw const DeviceEnrollmentException(
      'Не удалось надёжно сохранить состояние привязки устройства.',
    );
  }

  final receipt = await api.pair(
    baseUrl: baseUrl,
    bootstrapToken: bootstrapToken,
    pairingSessionId: pairingSessionId,
    deviceId: deviceId,
    metadata: metadata,
    idempotencyKey: idempotencyKey,
    ownerPublicKeySpkiB64: ownerPublicKey,
  );
  if (receipt.deviceId != deviceId || !_isDeviceToken(receipt.deviceToken)) {
    throw const DeviceEnrollmentException('Сервер вернул некорректную привязку.');
  }
  if (!await bridge.putEngineDeviceToken(receipt.deviceToken)) {
    throw const DeviceEnrollmentException(
      'Устройство не привязано: токен не сохранён в Keystore.',
    );
  }

  await store.writeDurably('engine', {
    'base_url': baseUrl,
    'device_id': deviceId,
    'device_enrollment_v1': true,
  });
  return receipt;
}

Future<DeviceEnrollmentReceipt> rotateAndStoreEngineDevice(
  LocalStore store,
  NativeBridge bridge, {
  required String baseUrl,
  DeviceEnrollmentApi api = const HttpDeviceEnrollmentApi(),
  Random? random,
}) async {
  if (!baseUrl.startsWith('https://')) {
    throw const DeviceEnrollmentException('Адрес движка должен использовать HTTPS.');
  }
  if (!await bridge.vaultAvailable()) {
    throw const DeviceEnrollmentException(
      'Устройство не привязано: Android Keystore недоступен.',
    );
  }
  final activeToken = await bridge.engineDeviceToken() ?? '';
  if (!_isDeviceToken(activeToken)) {
    throw const DeviceEnrollmentException('Нет действующего токена устройства.');
  }
  final saved = await store.read('engine') ?? <String, dynamic>{};
  final deviceId = saved['device_id'];
  if (saved['device_enrollment_v1'] != true || !_isDeviceId(deviceId)) {
    throw const DeviceEnrollmentException('Состояние привязки устройства неполно.');
  }
  final source = random ?? Random.secure();
  final retained = saved['base_url'] == baseUrl ? saved['rotation_request_id'] : null;
  final idempotencyKey = retained is String &&
          RegExp(r'^[A-Za-z0-9._:-]{16,128}$').hasMatch(retained)
      ? retained
      : _randomUrlSafe(source);
  final rotating = <String, dynamic>{
    'base_url': baseUrl,
    'device_id': deviceId,
    'device_enrollment_v1': true,
    'rotation_request_id': idempotencyKey,
  };
  if (!await store.writeDurably('engine', rotating)) {
    throw const DeviceEnrollmentException('Не удалось надёжно сохранить смену токена.');
  }
  final receipt = await api.rotate(
    baseUrl: baseUrl,
    activeDeviceToken: activeToken,
    idempotencyKey: idempotencyKey,
  );
  if (receipt.deviceId != deviceId || !_isDeviceToken(receipt.deviceToken)) {
    throw const DeviceEnrollmentException('Сервер вернул некорректную привязку.');
  }
  if (!await bridge.putEngineDeviceToken(receipt.deviceToken)) {
    throw const DeviceEnrollmentException(
      'Устройство не привязано: токен не сохранён в Keystore.',
    );
  }
  await store.writeDurably('engine', {
    'base_url': baseUrl,
    'device_id': deviceId,
    'device_enrollment_v1': true,
  });
  return receipt;
}

/// Revoke remotely first.  The local bearer and owner signing key stay intact
/// after an unconfirmed request.  Once the server confirms revocation both
/// local credentials are deleted; deletion is retryable and fail-closed.
Future<void> forgetEngineDevice(
  LocalStore store,
  NativeBridge bridge, {
  required String baseUrl,
  DeviceEnrollmentApi api = const HttpDeviceEnrollmentApi(),
}) async {
  if (!baseUrl.startsWith('https://')) {
    throw const DeviceEnrollmentException('Адрес движка должен использовать HTTPS.');
  }
  final activeToken = await bridge.engineDeviceToken() ?? '';
  if (activeToken.isEmpty) {
    if (!await bridge.deleteOwnerStepUpKey()) {
      throw const DeviceEnrollmentException(
        'Не удалось удалить ключ подтверждения владельца из Android Keystore.',
      );
    }
    await store.writeDurably('engine', {'base_url': baseUrl});
    return;
  }
  if (!_isDeviceToken(activeToken)) {
    throw const DeviceEnrollmentException('Токен устройства некорректен.');
  }
  await api.revoke(baseUrl: baseUrl, activeDeviceToken: activeToken);
  if (!await bridge.deleteEngineDeviceToken()) {
    throw const DeviceEnrollmentException('Не удалось удалить токен из Android Keystore.');
  }
  if (!await bridge.deleteOwnerStepUpKey()) {
    throw const DeviceEnrollmentException(
      'Не удалось удалить ключ подтверждения владельца из Android Keystore.',
    );
  }
  await store.writeDurably('engine', {'base_url': baseUrl});
}
