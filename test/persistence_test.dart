import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';

void main() {
  test('LocalStore: запись и чтение через диск', () async {
    final dir = Directory.systemTemp.createTempSync('signalai_store');
    addTearDown(() => dir.deleteSync(recursive: true));

    final store = LocalStore(directory: dir);
    await store.write('state', {'a': 1, 'b': 'два'});

    // Новый экземпляр — как новый запуск процесса.
    final reopened = LocalStore(directory: dir);
    final read = await reopened.read('state');
    expect(read, {'a': 1, 'b': 'два'});
  });

  test('LocalStore: битый файл не роняет чтение', () async {
    final dir = Directory.systemTemp.createTempSync('signalai_store');
    addTearDown(() => dir.deleteSync(recursive: true));

    File('${dir.path}/state.json').writeAsStringSync('{оборванный json');
    final store = LocalStore(directory: dir);
    expect(await store.read('state'), isNull);
  });

  test('настройки переживают перезапуск: риск, тумблеры, стратегии', () async {
    final store = LocalStore.inMemory();

    final first = LocalAnalysisRepository(store: store);
    await first.updateRiskProfile(deposit: 2500000, riskPercent: 1.0);
    await first.setChannelEnabled('push', true);
    await first.setNotificationEnabled('digest', true);
    await first.setStrategyEnabled('crypto', false);

    // «Перезапуск»: новый репозиторий, то же хранилище.
    final second = LocalAnalysisRepository(store: store);
    final settings = await second.fetchSettings();

    expect(settings.risk.deposit, 2500000);
    expect(settings.risk.riskPercent, 1.0);
    expect(settings.channels.firstWhere((c) => c.id == 'push').enabled, isTrue);
    expect(
      settings.notifications.firstWhere((n) => n.id == 'digest').enabled,
      isTrue,
    );
    expect(second.pushEnabled, isTrue);

    final strategies = await second.fetchStrategies();
    expect(
      strategies.packs.firstWhere((p) => p.id == 'crypto').enabled,
      isFalse,
    );
  });
}
