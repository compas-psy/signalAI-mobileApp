import 'local_store.dart';

/// Кто сейчас владеет состоянием на диске.
///
/// Фоновый контур делает полный пересчёт и пишет те же файлы, что и интерфейс:
/// дайджест, журнал сделок, настройки. Два писателя без дисциплины дают не
/// битый файл (запись атомарна), а потерянное обновление: фон сохраняет журнал,
/// прочитанный минуту назад, и закрытая за это время сделка исчезает.
///
/// Блокировка рекомендательная и по отметке времени: владелец обновляет её
/// раз в минуту, чужая отметка старше [staleAfter] считается брошенной. Так
/// падение интерфейса не запирает фон навсегда — а именно это сделал бы
/// «честный» флаг, который снимается только явно.
class StateLock {
  StateLock(this._store);

  final LocalStore _store;

  /// Владелец из интерфейса.
  static const ui = 'ui';

  /// Владелец из фонового изолята.
  static const monitor = 'monitor';

  /// Через сколько отметка считается брошенной.
  ///
  /// Две минуты — вдвое больше, чем период обновления у интерфейса: одна
  /// пропущенная отметка (пауза приложения, долгий пересчёт) не должна
  /// отдавать владение.
  static const staleAfter = Duration(minutes: 2);

  static const _file = 'state_owner';

  /// Отмечает владение за [owner].
  Future<void> heartbeat(String owner) => _store.write(_file, {
        'owner': owner,
        'at': DateTime.now().toIso8601String(),
      });

  /// Держит ли состояние кто-то другой прямо сейчас.
  Future<bool> heldByOther(String owner, {DateTime? now}) async {
    final json = await _store.read(_file);
    if (json == null) return false;
    final holder = json['owner'] as String?;
    if (holder == null || holder == owner) return false;
    final at = DateTime.tryParse(json['at'] as String? ?? '');
    if (at == null) return false;
    return (now ?? DateTime.now()).difference(at) < staleAfter;
  }

  /// Отпускает владение, если оно наше.
  ///
  /// Чужую отметку не трогаем: снять её означало бы разрешить себе писать
  /// поверх работающего владельца.
  Future<void> release(String owner) async {
    final json = await _store.read(_file);
    if ((json?['owner'] as String?) != owner) return;
    await _store.write(_file, {'owner': '', 'at': ''});
  }
}
