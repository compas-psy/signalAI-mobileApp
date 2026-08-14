import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/monitor/runtime_error_recorder.dart';

void main() {
  test('summary counts retained runtime events by diagnostic kind', () async {
    final recorder = RuntimeErrorRecorder(
      store: LocalStore.inMemory(),
      identity: const RuntimeBuildIdentity(appVersion: 'test', sourceSha: 'sha'),
    );

    await recorder.record(
      kind: RuntimeErrorKind.sandboxReconciliation,
      error: 'repair-1',
    );
    await recorder.record(
      kind: RuntimeErrorKind.chartLoad,
      error: 'chart',
    );
    await recorder.record(
      kind: RuntimeErrorKind.sandboxReconciliation,
      error: 'repair-2',
    );

    final summary = await recorder.summary();

    expect(summary.total, 3);
    expect(summary.byKind[RuntimeErrorKind.sandboxReconciliation], 2);
    expect(summary.byKind[RuntimeErrorKind.chartLoad], 1);
    expect(summary.latestAt, isNotNull);
  });
}
