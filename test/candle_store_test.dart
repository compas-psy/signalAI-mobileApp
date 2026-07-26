import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/market/candle_store.dart';
import 'package:signalai/domain/analysis/candle.dart';

/// История свечей должна докачиваться, а не качаться заново: год часовиков по
/// сорока сериям — это сотни запросов, и повторять их при каждом прогоне
/// незачем, прошедшие часы уже не изменятся.
void main() {
  final t0 = DateTime.utc(2026, 3, 2, 10);

  Candle at(int hour, double close) => Candle(
        time: t0.add(Duration(hours: hour)),
        open: close,
        high: close,
        low: close,
        close: close,
        volume: 1,
      );

  CandleStore store() => CandleStore(LocalStore.inMemory());

  test('пустое хранилище требует полной загрузки', () async {
    final s = store();
    final known = await s.load('SiZ5|60');
    expect(known.candles, isEmpty);
    expect(s.incrementalStart(known, t0), isNull);
  });

  test('после сохранения догружается только хвост', () async {
    final s = store();
    await s.merge('SiZ5|60', [at(0, 100), at(1, 101)], coveredFrom: t0);

    final known = await s.load('SiZ5|60');
    expect(known.candles, hasLength(2));
    expect(s.incrementalStart(known, t0), at(1, 101).time,
        reason: 'последняя свеча перезапрашивается — её час мог быть не закрыт');
  });

  test('запрос глубже уже скачанного требует полной загрузки', () async {
    final s = store();
    await s.merge('SiZ5|60', [at(0, 100)], coveredFrom: t0);

    final known = await s.load('SiZ5|60');
    expect(
      s.incrementalStart(known, t0.subtract(const Duration(days: 30))),
      isNull,
      reason: 'этой глубины мы ещё не просили — хвостом не обойтись',
    );
  });

  test('глубина покрытия только растёт', () async {
    final s = store();
    final deeper = t0.subtract(const Duration(days: 30));
    await s.merge('SiZ5|60', [at(0, 100)], coveredFrom: deeper);
    // Следующий запрос мельче — уже скачанную глубину он не отменяет.
    await s.merge('SiZ5|60', [at(1, 101)], coveredFrom: t0);

    final known = await s.load('SiZ5|60');
    expect(known.coveredFrom, deeper);
    expect(s.incrementalStart(known, deeper), isNotNull);
  });

  test('свежие данные перекрывают незакрытую свечу', () async {
    final s = store();
    await s.merge('SiZ5|60', [at(0, 100), at(1, 101)], coveredFrom: t0);
    // Тот же час, но цена доехала: должна остаться свежая.
    final merged = await s.merge('SiZ5|60', [at(1, 105), at(2, 106)], coveredFrom: t0);

    expect(merged.map((c) => c.close), [100, 105, 106]);
  });

  test('история переживает пересоздание хранилища на том же диске', () async {
    final disk = LocalStore.inMemory();
    await CandleStore(disk).merge('SiZ5|60', [at(0, 100), at(1, 101)], coveredFrom: t0);

    final restored = await CandleStore(disk).load('SiZ5|60');
    expect(restored.candles.map((c) => c.close), [100, 101]);
    expect(restored.coveredFrom, t0);
  });

  test('размер серии ограничен — телефон не архив биржи', () async {
    final s = store();
    await s.merge(
      'SiZ5|60',
      [for (var i = 0; i < CandleStore.maxCandles + 500; i++) at(i, 100 + i.toDouble())],
      coveredFrom: t0,
    );

    final known = await s.load('SiZ5|60');
    expect(known.candles, hasLength(CandleStore.maxCandles));
    // Обрезается начало: свежие свечи важнее самых старых.
    expect(known.candles.last.close, 100 + (CandleStore.maxCandles + 499).toDouble());
  });

  test('символы с разделителями дают корректное имя файла', () async {
    final disk = LocalStore.inMemory();
    final s = CandleStore(disk);
    await s.merge(CandleStore.keyFor('BTCUSDT', 'h1'), [at(0, 100)], coveredFrom: t0);

    final restored = await CandleStore(disk).load(CandleStore.keyFor('BTCUSDT', 'h1'));
    expect(restored.candles, hasLength(1));
  });
}
