import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/native_bridge.dart';

import 'support/offline_http.dart';

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

  test('сбой каталога при старте не превращается в вечную амнезию', () async {
    final dir = Directory.systemTemp.createTempSync('signalai_store');
    addTearDown(() => dir.deleteSync(recursive: true));

    // Мост, который падает на первых вызовах — как канал на холодном старте.
    final bridge = _FlakyBridge(dir.path, failures: 2);
    final store = LocalStore(bridge: bridge);

    // Пока каталога нет, запись живёт в памяти — и не теряется.
    await store.write('state', {'a': 1});
    expect(await store.persistent, isFalse);

    // Канал ожил: следующая запись уходит на диск вместе с накопленным.
    await store.write('ledger', {'b': 2});
    expect(await store.persistent, isTrue);

    // «Перезапуск»: новый экземпляр с рабочим мостом читает всё с диска.
    final reopened = LocalStore(bridge: _FlakyBridge(dir.path, failures: 0));
    expect(await reopened.read('state'), {'a': 1},
        reason: 'записанное до появления диска не выбрасывается');
    expect(await reopened.read('ledger'), {'b': 2});
  });

  test('настройки переживают перезапуск: риск, тумблеры, стратегии', () async {
    final store = LocalStore.inMemory();

    final first = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store);
    await first.updateRiskProfile(deposit: 2500000, riskPercent: 1.0);
    await first.setChannelEnabled('push', true);
    await first.setNotificationEnabled('alerts', true);
    await first.setStrategyEnabled('crypto', false);

    // «Перезапуск»: новый репозиторий, то же хранилище.
    final second = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store);
    final settings = await second.fetchSettings();

    expect(settings.risk.deposit, 2500000);
    expect(settings.risk.riskPercent, 1.0);
    expect(settings.channels.firstWhere((c) => c.id == 'push').enabled, isTrue);
    // Тумблера «Автопересчёт раз в час» здесь больше нет: он не был подключён
    // ни к чему. Проверяем тот, который существует.
    expect(
      settings.notifications.firstWhere((n) => n.id == 'alerts').enabled,
      isTrue,
    );
    expect(second.pushEnabled, isTrue);

    final strategies = await second.fetchStrategies();
    expect(
      strategies.packs.firstWhere((p) => p.id == 'crypto').enabled,
      isFalse,
    );
  });

  test('журнал пересчётов переживает перезапуск', () async {
    // Владелец не мог отличить «пересчиталось и ничего не нашлось» от «не
    // пересчитывалось»: настройка «раз в час» была, а доказательства не было.
    final store = LocalStore.inMemory();
    final first = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store);
    await first.fetchSettings();
    expect(first.digestRuns, isEmpty, reason: 'до первого прогона журнал пуст');

    await store.write('digest_runs', {
      'items': [
        DigestRun(
          at: DateTime.utc(2026, 7, 28, 9),
          ideas: 2,
          candidates: 18,
          rejected: 16,
          foreground: false,
        ).toJson(),
        DigestRun(
          at: DateTime.utc(2026, 7, 28, 10),
          ideas: 0,
          candidates: 0,
          rejected: 0,
          foreground: true,
          note: 'не удалось: сеть',
        ).toJson(),
      ],
    });

    final second = LocalAnalysisRepository(
      iss: offlineIss(),
      bybit: offlineBybit(),
      store: store);
    await second.fetchSettings();

    // От новых к старым: последний прогон первым.
    expect(second.digestRuns.first.failed, isTrue);
    expect(second.digestRuns.last.foreground, isFalse,
        reason: 'фоновый прогон отличим от экранного');
    expect(second.digestRuns.last.ideas, 2);
  });
}


/// Мост, у которого первые [failures] запросов каталога падают.
class _FlakyBridge extends NativeBridge {
  _FlakyBridge(this.path, {required this.failures});

  final String path;
  int failures;

  @override
  Future<String?> filesDir() async {
    if (failures > 0) {
      failures--;
      return null;
    }
    return path;
  }
}
