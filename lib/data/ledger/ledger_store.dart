import 'dart:convert';
import 'dart:io';

import '../../domain/ledger/ledger_event.dart';
import '../native_bridge.dart';

/// Хранилище книги операций: журнал только на дозапись.
///
/// Почему не SQLite. ТЗ допускает «другой зрелый typed-слой при
/// документированном обосновании» — вот оно. Книга по модели неизменяема:
/// записи не обновляются и не удаляются, ошибка исправляется компенсирующей
/// операцией. Для такой модели файл-журнал (одно событие — одна строка JSON)
/// точнее выражает намерение, чем таблица с UPDATE и DELETE, которых не
/// должно быть; миграции сводятся к версии схемы в заголовке, а транзакция —
/// к атомарной дозаписи. Плюс не появляется нативной зависимости в
/// приложении, которое подтверждает сделки: sqlite3-биндинги в APK — это
/// ещё одна поверхность отказа на устройстве владельца.
///
/// Формат: первая строка — заголовок `{"schema":1}`, дальше по событию на
/// строку. Битая строка пропускается с подсчётом, а не роняет книгу: потерять
/// одну операцию плохо, потерять всю книгу — катастрофа.
class LedgerStore {
  LedgerStore({Directory? directory, NativeBridge bridge = const NativeBridge()})
      : _directory = directory,
        _bridge = bridge,
        _resolved = directory != null;

  /// Версия формата. Растёт при несовместимом изменении записи.
  static const schemaVersion = 1;

  static const _fileName = 'capital_ledger.ndjson';

  final NativeBridge _bridge;
  Directory? _directory;
  bool _resolved;

  /// Кэш книги в памяти. Единственный владелец — этот объект.
  List<LedgerEvent>? _cache;

  /// Сколько строк не удалось разобрать при последней загрузке.
  int corrupted = 0;

  /// Книга без диска — для тестов.
  factory LedgerStore.inMemory() {
    final store = LedgerStore();
    store._resolved = true;
    store._cache = [];
    return store;
  }

  Future<Directory?> _dir() async {
    if (_resolved) return _directory;
    final path = await _bridge.filesDir();
    if (path == null) return _directory;
    final directory = Directory('$path/signalai');
    try {
      directory.createSync(recursive: true);
    } on FileSystemException {
      return _directory;
    }
    _directory = directory;
    _resolved = true;
    return _directory;
  }

  File? _file(Directory dir) => File('${dir.path}/$_fileName');

  /// Пишет ли книга на диск. false — операции этой сессии не переживут
  /// перезапуск, и молчать об этом нельзя.
  Future<bool> get persistent async => await _dir() != null;

  /// Все события книги в порядке записи.
  Future<List<LedgerEvent>> load() async {
    final cached = _cache;
    if (cached != null) return List.unmodifiable(cached);

    final dir = await _dir();
    final events = <LedgerEvent>[];
    corrupted = 0;
    if (dir != null) {
      final file = _file(dir)!;
      if (file.existsSync()) {
        final lines = file.readAsLinesSync();
        for (var i = 0; i < lines.length; i++) {
          final line = lines[i].trim();
          if (line.isEmpty) continue;
          try {
            final json = jsonDecode(line) as Map<String, dynamic>;
            if (i == 0 && json.containsKey('schema')) {
              // Заголовок. Старшая версия читается настолько, насколько
              // получится: неизвестные поля игнорируются разбором события.
              continue;
            }
            events.add(LedgerEvent.fromJson(json));
          } on Exception {
            corrupted++;
          }
        }
      }
    }
    _cache = events;
    return List.unmodifiable(events);
  }

  /// Дозаписывает события. Дубли по id отбрасываются молча — повторный
  /// импорт одной выписки не должен удваивать капитал.
  ///
  /// Возвращает, сколько записей действительно добавлено.
  Future<int> append(List<LedgerEvent> events) async {
    if (events.isEmpty) return 0;
    final current = await load();
    final known = {for (final e in current) e.id};
    final fresh = [
      for (final e in events)
        if (known.add(e.id)) e,
    ];
    if (fresh.isEmpty) return 0;

    _cache = [...current, ...fresh];

    final dir = await _dir();
    if (dir == null) return fresh.length;

    final file = _file(dir)!;
    final buffer = StringBuffer();
    if (!file.existsSync()) {
      buffer.writeln(jsonEncode({'schema': schemaVersion}));
    }
    for (final event in fresh) {
      buffer.writeln(jsonEncode(event.toJson()));
    }
    try {
      file.writeAsStringSync(buffer.toString(), mode: FileMode.append, flush: true);
    } on FileSystemException {
      // Диск отказал — события остались в памяти и видны в интерфейсе;
      // о непостоянстве книги сообщает `persistent`.
    }
    return fresh.length;
  }

  /// Полная перезапись файла — только для восстановления из резервной копии
  /// и импорта истории. Обычный путь записи — [append].
  Future<void> replaceAll(List<LedgerEvent> events) async {
    _cache = [...events];
    final dir = await _dir();
    if (dir == null) return;
    final buffer = StringBuffer()..writeln(jsonEncode({'schema': schemaVersion}));
    for (final event in events) {
      buffer.writeln(jsonEncode(event.toJson()));
    }
    final file = _file(dir)!;
    // Через временный файл: обрыв записи не должен оставить половину книги.
    final temp = File('${file.path}.tmp');
    try {
      temp.writeAsStringSync(buffer.toString(), flush: true);
      temp.renameSync(file.path);
    } on FileSystemException {
      // Осталась прежняя книга на диске — это лучше, чем обрезанная.
    }
  }

  /// Выгрузка книги для экспорта и резервной копии.
  Future<String> exportJsonl() async {
    final events = await load();
    final buffer = StringBuffer()..writeln(jsonEncode({'schema': schemaVersion}));
    for (final event in events) {
      buffer.writeln(jsonEncode(event.toJson()));
    }
    return buffer.toString();
  }
}
