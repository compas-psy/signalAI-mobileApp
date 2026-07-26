import 'dart:convert';
import 'dart:io';

import 'native_bridge.dart';

/// JSON-хранилище настроек и кэшей на устройстве.
///
/// Файлы лежат в приватном каталоге приложения (`filesDir` через нативный
/// мост) — без внешних зависимостей и без прав на диск. Если каталога нет
/// (тесты, платформа без моста), хранилище честно работает в памяти:
/// интерфейс тот же, персистентности нет.
class LocalStore {
  LocalStore({Directory? directory, NativeBridge bridge = const NativeBridge()})
      : _directory = directory,
        _bridge = bridge,
        _resolved = directory != null;

  final NativeBridge _bridge;
  Directory? _directory;
  bool _resolved;

  final Map<String, Map<String, dynamic>> _memory = {};

  /// Хранилище без диска — для тестов.
  factory LocalStore.inMemory() {
    final store = LocalStore();
    store._resolved = true;
    return store;
  }

  Future<Directory?> _dir() async {
    if (_resolved) return _directory;
    _resolved = true;
    if (_directory != null) return _directory;
    final path = await _bridge.filesDir();
    if (path != null) {
      _directory = Directory('$path/signalai');
      try {
        _directory!.createSync(recursive: true);
      } on FileSystemException {
        _directory = null;
      }
    }
    return _directory;
  }

  File _file(Directory dir, String name) => File('${dir.path}/$name.json');

  /// Читает документ. null — не сохраняли или файл нечитаем.
  Future<Map<String, dynamic>?> read(String name) async {
    final dir = await _dir();
    if (dir == null) return _memory[name];
    try {
      final file = _file(dir, name);
      if (!file.existsSync()) return null;
      return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
    } on Exception {
      // Битый файл не должен ронять запуск — считаем, что данных нет.
      return null;
    }
  }

  /// Пишет документ целиком (атомарно через временный файл).
  Future<void> write(String name, Map<String, dynamic> value) async {
    final dir = await _dir();
    if (dir == null) {
      _memory[name] = value;
      return;
    }
    try {
      final tmp = File('${dir.path}/$name.json.tmp');
      tmp.writeAsStringSync(jsonEncode(value));
      tmp.renameSync(_file(dir, name).path);
    } on FileSystemException {
      // Диск недоступен — данные останутся в памяти процесса.
      _memory[name] = value;
    }
  }
}
