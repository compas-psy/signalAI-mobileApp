import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/state_lock.dart';

/// Фон и интерфейс пишут одни и те же файлы. Блокировка решает, кто из них
/// имеет право на запись прямо сейчас — ошибка здесь стоит потерянного
/// журнала сделок, поэтому проверяется каждый переход.
void main() {
  final now = DateTime.utc(2026, 3, 10, 12);

  test('свободная блокировка никому не мешает', () async {
    final lock = StateLock(LocalStore.inMemory());
    expect(await lock.heldByOther(StateLock.monitor, now: now), isFalse);
  });

  test('своя отметка чужой не считается', () async {
    final lock = StateLock(LocalStore.inMemory());
    await lock.heartbeat(StateLock.ui);
    expect(await lock.heldByOther(StateLock.ui), isFalse);
  });

  test('свежая чужая отметка запрещает запись', () async {
    final lock = StateLock(LocalStore.inMemory());
    await lock.heartbeat(StateLock.ui);
    expect(await lock.heldByOther(StateLock.monitor), isTrue);
  });

  test('брошенная отметка владение не держит', () async {
    final store = LocalStore.inMemory();
    // Отметка часовой давности: интерфейс не отвечает, фон обязан работать.
    await store.write('state_owner', {
      'owner': StateLock.ui,
      'at': now.subtract(const Duration(hours: 1)).toIso8601String(),
    });

    expect(await StateLock(store).heldByOther(StateLock.monitor, now: now), isFalse);
  });

  test('граница устаревания — не раньше и не позже', () async {
    final store = LocalStore.inMemory();
    Future<bool> heldAfter(Duration age) async {
      await store.write('state_owner', {
        'owner': StateLock.ui,
        'at': now.subtract(age).toIso8601String(),
      });
      return StateLock(store).heldByOther(StateLock.monitor, now: now);
    }

    expect(await heldAfter(StateLock.staleAfter - const Duration(seconds: 1)), isTrue);
    expect(await heldAfter(StateLock.staleAfter + const Duration(seconds: 1)), isFalse);
  });

  test('освобождение снимает владение', () async {
    final lock = StateLock(LocalStore.inMemory());
    await lock.heartbeat(StateLock.ui);
    await lock.release(StateLock.ui);
    expect(await lock.heldByOther(StateLock.monitor), isFalse);
  });

  test('чужое владение снять нельзя', () async {
    final lock = StateLock(LocalStore.inMemory());
    await lock.heartbeat(StateLock.ui);
    // Фон пытается освободить блокировку интерфейса — это должно быть no-op,
    // иначе он разрешит себе писать поверх работающего владельца.
    await lock.release(StateLock.monitor);
    expect(await lock.heldByOther(StateLock.monitor), isTrue);
  });

  test('битая отметка времени владение не держит', () async {
    final store = LocalStore.inMemory();
    await store.write('state_owner', {'owner': StateLock.ui, 'at': 'мусор'});
    expect(await StateLock(store).heldByOther(StateLock.monitor, now: now), isFalse);
  });
}
