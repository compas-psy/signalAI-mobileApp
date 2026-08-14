import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/monitor/runtime_error_recorder.dart';

void main() {
  group('RuntimeErrorRecorder', () {
    late Directory directory;

    setUp(() {
      directory = Directory.systemTemp.createTempSync('signalai-runtime-errors-');
    });

    tearDown(() {
      if (directory.existsSync()) directory.deleteSync(recursive: true);
    });

    test('redacts credentials before persistence and stores build identity', () async {
      const secretBearer = 'secret-bearer-123';
      const secretToken = 'secret-token-456';
      const secretApiKey = 'secret-api-key-789';
      final store = LocalStore(directory: directory);
      final recorder = RuntimeErrorRecorder(
        store: store,
        identity: const RuntimeBuildIdentity(
          appVersion: '1.2.3+45',
          sourceSha: '0123456789abcdef0123456789abcdef01234567',
        ),
      );

      await recorder.record(
        kind: RuntimeErrorKind.flutter,
        error: StateError(
          'Authorization: Bearer $secretBearer token=$secretToken api_key: $secretApiKey',
        ),
        stackTrace: StackTrace.fromString('password=hunter2\nBearer stack-secret'),
      );

      final raw = File('${directory.path}/runtime_error_history.json').readAsStringSync();
      expect(raw, isNot(contains(secretBearer)));
      expect(raw, isNot(contains(secretToken)));
      expect(raw, isNot(contains(secretApiKey)));
      expect(raw, isNot(contains('hunter2')));
      expect(raw, isNot(contains('stack-secret')));

      final events = await recorder.events();
      expect(events, hasLength(1));
      expect(events.single.kind, RuntimeErrorKind.flutter);
      expect(events.single.appVersion, '1.2.3+45');
      expect(events.single.sourceSha, '0123456789abcdef0123456789abcdef01234567');
      expect(events.single.error, contains('[REDACTED]'));
      expect(events.single.stackTrace, contains('[REDACTED]'));
    });

    test('keeps only newest events within configured bound', () async {
      final recorder = RuntimeErrorRecorder(
        store: LocalStore(directory: directory),
        identity: const RuntimeBuildIdentity(appVersion: 'test', sourceSha: 'test-sha'),
        maxEvents: 3,
      );

      for (var i = 0; i < 5; i++) {
        await recorder.record(kind: RuntimeErrorKind.async, error: 'error-$i');
      }

      final events = await recorder.events();
      expect(events.map((event) => event.error).toList(), <String>[
        'error-2',
        'error-3',
        'error-4',
      ]);
    });

    test('persists history across recorder and LocalStore instances', () async {
      final first = RuntimeErrorRecorder(
        store: LocalStore(directory: directory),
        identity: const RuntimeBuildIdentity(appVersion: 'first', sourceSha: 'sha-1'),
      );
      await first.record(kind: RuntimeErrorKind.async, error: 'survives restart');

      final second = RuntimeErrorRecorder(
        store: LocalStore(directory: directory),
        identity: const RuntimeBuildIdentity(appVersion: 'second', sourceSha: 'sha-2'),
      );
      final events = await second.events();

      expect(events, hasLength(1));
      expect(events.single.error, 'survives restart');
      expect(events.single.appVersion, 'first');
      expect(events.single.sourceSha, 'sha-1');
    });

    test('default build identity is always non-empty', () {
      expect(RuntimeBuildIdentity.current.appVersion, isNotEmpty);
      expect(RuntimeBuildIdentity.current.sourceSha, isNotEmpty);
    });
  });
}
