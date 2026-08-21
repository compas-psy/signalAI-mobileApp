import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_config.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/mock/demo_repository.dart';
import 'package:signalai/data/native_bridge.dart';
import 'package:signalai/state/app_controller.dart';

/// Порядок на холодном старте: сначала адрес движка, потом запросы к нему.
///
/// Проверяется не «поле прочиталось», а гонка, из-за которой приложение с
/// полностью заданными настройками показывало «Адрес движка не задан».
/// Чтение адреса идёт через нативный мост — то есть медленнее, чем первый
/// кадр, — и раньше никто его не ждал.
void main() {
  /// Хранилище, которое отвечает не мгновенно. Мгновенное скрыло бы гонку:
  /// на быстром ответе порядок соблюдается сам собой.
  LocalStore slow(Map<String, dynamic>? saved) => _SlowStore(saved);

  tearDown(() {
    ApiConfig.setBaseUrl('');
    ApiConfig.setDeviceToken('');
  });

  test('legacy credential не открывает API и адрес всё равно восстанавливается', () async {
    final controller = AppController(
      DemoRepository(),
      bridge: _VaultBridge(),
      prefs: slow({
        'base_url': 'https://engine.example.ru',
        'device_token': 'abc',
      }),
    );
    addTearDown(controller.dispose);

    await controller.loadPortfolio();

    // Адрес применён к моменту запроса — значит запрос ушёл по нему, а не
    // в пустоту.
    expect(ApiConfig.baseUrl, 'https://engine.example.ru');
    expect(ApiConfig.deviceToken, isEmpty);
    expect(
      (controller.engineAuthIssue),
      contains('привязку заново'),
      reason: 'bootstrap secret нельзя мигрировать как business bearer',
    );
    final state = controller.portfolio!;
    expect(state.isAvailable, isFalse, reason: 'сети в тесте нет');
    // Ключевое: причина — обрыв связи, а не «адреса нет». Второе означало
    // бы, что запрос ушёл раньше, чем приехали настройки.
    expect(state.unavailableReason, isNot(contains('Адрес движка не задан')));
  });

  test('без сохранённого адреса причина названа и ведёт к настройкам', () async {
    final controller = AppController(DemoRepository(), prefs: slow(null));
    addTearDown(controller.dispose);

    await controller.loadPortfolio();

    final state = controller.portfolio!;
    expect(state.isAvailable, isFalse);
    expect(state.unavailableReason, contains('Адрес движка не задан'));
    // Диагноз без выхода из него — половина ответа: текст обязан говорить,
    // где адрес задаётся.
    expect(state.unavailableReason, contains('Подключения'));
  });

  test('смена адреса выбрасывает прежний ответ движка', () async {
    final controller = AppController(DemoRepository(), prefs: slow(null));
    addTearDown(controller.dispose);

    await controller.loadPortfolio();
    expect(controller.portfolio!.unavailableReason, contains('не задан'));

    await controller.setEngineBaseUrl('https://engine.example.ru');

    // Ответ старого адреса не переживает переезд: иначе «Портфель» показывал
    // бы отказ прежнего сервера до ручного нажатия.
    expect(
      controller.portfolio!.unavailableReason,
      isNot(contains('Адрес движка не задан')),
    );
  });

  test('без Keystore legacy token удаляется из JSON и auth закрывается',
      () async {
    final store = _SlowStore({
      'base_url': 'https://engine.example.ru',
      'device_token': 'plaintext-legacy',
    });
    final controller = AppController(
      DemoRepository(),
      prefs: store,
      bridge: const _NoVaultBridge(),
    );
    addTearDown(controller.dispose);

    await controller.loadPortfolio();

    expect(ApiConfig.deviceToken, isEmpty);
    expect(controller.engineAuthIssue, contains('не привязано'));
    expect(store.lastWritten, {'base_url': 'https://engine.example.ru'});
  });
}

class _SlowStore extends LocalStore {
  _SlowStore(this._saved);

  final Map<String, dynamic>? _saved;
  Map<String, dynamic>? lastWritten;

  @override
  Future<Map<String, dynamic>?> read(String name) async {
    await Future<void>.delayed(const Duration(milliseconds: 30));
    return name == 'engine' ? _saved : null;
  }

  @override
  Future<void> write(String name, Map<String, dynamic> value) async {
    if (name == 'engine') lastWritten = Map<String, dynamic>.from(value);
  }
}

class _VaultBridge extends NativeBridge {
  String? token;

  @override
  Future<bool> vaultAvailable() async => true;

  @override
  Future<bool> putEngineDeviceToken(String value) async {
    token = value;
    return true;
  }

  @override
  Future<String?> engineDeviceToken() async => token;
}

class _NoVaultBridge extends NativeBridge {
  const _NoVaultBridge();

  @override
  Future<bool> vaultAvailable() async => false;
}
