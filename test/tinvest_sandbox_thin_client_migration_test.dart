import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/integrations_client.dart';
import 'package:signalai/data/api/sandbox_mirroring_engine_client.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';

const _slot = 'tinvest_sandbox_trade';

ServerIntegration _serverSandbox({
  String slot = _slot,
  String venue = 'TINVEST',
  String environment = 'sandbox',
  String purpose = 'trade',
  bool configured = true,
  List<String> fields = const ['token'],
}) =>
    ServerIntegration(
      slot: slot,
      venue: venue,
      title: 'T-Invest Sandbox',
      purpose: purpose,
      environment: environment,
      fields: fields,
      configured: configured,
      required: false,
    );

class _FakeVault extends SecureVault {
  _FakeVault(this.token);

  String? token;
  int deletes = 0;

  @override
  Future<String?> apiKey({required String exchange, required String mode}) async =>
      exchange == 'tinvest' && mode == 'sandbox' ? token : null;

  @override
  Future<bool> hasKeys({
    required String exchange,
    required String mode,
    bool needsSecret = true,
  }) async =>
      exchange == 'tinvest' && mode == 'sandbox' && token != null;

  @override
  Future<void> deleteKeys({required String exchange, required String mode}) async {
    if (exchange == 'tinvest' && mode == 'sandbox') {
      deletes += 1;
      token = null;
    }
  }
}

class _FakeIntegrationsClient extends IntegrationsClient {
  _FakeIntegrationsClient({
    this.listed = const [],
    this.saved,
    this.saveError,
  });

  List<ServerIntegration> listed;
  ServerIntegration? saved;
  Object? saveError;
  final List<Map<String, String>> saveCalls = [];

  @override
  Future<List<ServerIntegration>> list() async => listed;

  @override
  Future<ServerIntegration> save(
    String slot,
    Map<String, String> values,
  ) async {
    saveCalls.add(Map<String, String>.from(values));
    if (saveError case final error?) throw error;
    return saved ?? _serverSandbox();
  }
}

LocalAnalysisRepository _repository(_FakeVault vault) => LocalAnalysisRepository(
      store: LocalStore.inMemory(),
      vault: vault,
    );

void main() {
  setUp(() {
    TInvestSandboxAccess.detachForTest();
  });

  test('uploads existing Keystore token then deletes local copy only after exact server confirmation', () async {
    final vault = _FakeVault('LOCAL-SANDBOX-TOKEN');
    final repo = _repository(vault);
    final server = _FakeIntegrationsClient(saved: _serverSandbox());
    TInvestSandboxAccess.attach(repo);

    final migrated = await TInvestSandboxAccess.migrateToServer(server);

    expect(migrated, isTrue);
    expect(server.saveCalls, [
      {'token': 'LOCAL-SANDBOX-TOKEN'}
    ]);
    expect(vault.token, isNull);
    expect(vault.deletes, 1);
  });

  test('failed server upload keeps Keystore token', () async {
    final vault = _FakeVault('LOCAL-SANDBOX-TOKEN');
    final repo = _repository(vault);
    final server = _FakeIntegrationsClient(saveError: StateError('offline'));
    TInvestSandboxAccess.attach(repo);

    await expectLater(
      TInvestSandboxAccess.migrateToServer(server),
      throwsStateError,
    );

    expect(vault.token, 'LOCAL-SANDBOX-TOKEN');
    expect(vault.deletes, 0);
  });

  test('malformed server confirmation keeps Keystore token and fails closed', () async {
    final vault = _FakeVault('LOCAL-SANDBOX-TOKEN');
    final repo = _repository(vault);
    final server = _FakeIntegrationsClient(
      saved: _serverSandbox(environment: 'live'),
    );
    TInvestSandboxAccess.attach(repo);

    final migrated = await TInvestSandboxAccess.migrateToServer(server);

    expect(migrated, isFalse);
    expect(vault.token, 'LOCAL-SANDBOX-TOKEN');
    expect(vault.deletes, 0);
  });

  test('configured server slot is overwritten from leftover Keystore token before local deletion', () async {
    final vault = _FakeVault('LEFTOVER-TOKEN');
    final repo = _repository(vault);
    final server = _FakeIntegrationsClient(
      listed: [_serverSandbox()],
      saved: _serverSandbox(),
    );
    TInvestSandboxAccess.attach(repo);

    expect(await TInvestSandboxAccess.configured(server), isTrue);
    expect(await TInvestSandboxAccess.migrateToServer(server), isTrue);

    expect(server.saveCalls, [
      {'token': 'LEFTOVER-TOKEN'}
    ]);
    expect(vault.token, isNull);
    expect(vault.deletes, 1);
  });

  test('configured server slot without a local legacy token is already migrated', () async {
    final vault = _FakeVault(null);
    final repo = _repository(vault);
    final server = _FakeIntegrationsClient(listed: [_serverSandbox()]);
    TInvestSandboxAccess.attach(repo);

    expect(await TInvestSandboxAccess.migrateToServer(server), isTrue);

    expect(server.saveCalls, isEmpty);
    expect(vault.deletes, 0);
  });
}
