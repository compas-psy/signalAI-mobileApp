import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_config.dart';
import 'package:signalai/data/api/engine_runtime.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/native_bridge.dart';

class _Vault extends NativeBridge {
  _Vault({this.available = true});

  final bool available;
  String? token;

  @override
  Future<bool> vaultAvailable() async => available;

  @override
  Future<bool> putEngineDeviceToken(String value) async {
    if (!available) return false;
    token = value;
    return true;
  }

  @override
  Future<String?> engineDeviceToken() async => available ? token : null;
}

void main() {
  tearDown(() {
    ApiConfig.setBaseUrl('');
    ApiConfig.setDeviceToken('');
  });

  test('legacy plaintext мигрирует в Keystore и исчезает из JSON', () async {
    final store = LocalStore.inMemory();
    final vault = _Vault();
    await store.write('engine', {
      'base_url': 'https://engine.example.ru',
      'device_token': 'legacy-secret',
    });

    final credentials = await restoreEngineRuntime(store, vault);

    expect(credentials.ready, isTrue);
    expect(credentials.deviceToken, 'legacy-secret');
    expect(vault.token, 'legacy-secret');
    expect(await store.read('engine'), {
      'base_url': 'https://engine.example.ru',
    });
  });

  test('background-изолят восстанавливает runtime token из Keystore',
      () async {
    final store = LocalStore.inMemory();
    final vault = _Vault()..token = 'runtime-secret';
    await store.write('engine', {
      'base_url': 'https://engine.example.ru',
    });

    final credentials = await restoreEngineRuntime(store, vault);

    expect(credentials.ready, isTrue);
    expect(ApiConfig.baseUrl, 'https://engine.example.ru');
    expect(ApiConfig.deviceToken, 'runtime-secret');
  });

  test('без Keystore auth fail closed и plaintext не остаётся', () async {
    final store = LocalStore.inMemory();
    await store.write('engine', {
      'base_url': 'https://engine.example.ru',
      'device_token': 'must-not-survive',
    });

    final credentials = await restoreEngineRuntime(
      store,
      _Vault(available: false),
    );

    expect(credentials.ready, isFalse);
    expect(credentials.issue, contains('не привязано'));
    expect(ApiConfig.deviceToken, isEmpty);
    expect(await store.read('engine'), {
      'base_url': 'https://engine.example.ru',
    });
  });
}
