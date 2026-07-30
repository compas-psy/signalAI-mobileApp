import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/api_config.dart';
import 'package:signalai/data/api/engine_client.dart';

/// Адрес движка, задаваемый в приложении.
///
/// До этого он существовал только как параметр сборки: ключи бирж приняты,
/// данные идут, а лента идей пуста — и объяснения на экране не было.
void main() {
  tearDown(() => ApiConfig.setBaseUrl(''));

  test('заданный адрес перекрывает сборочный', () {
    expect(ApiConfig.baseUrl, ApiConfig.compiledBaseUrl);
    expect(ApiConfig.isOverridden, isFalse);

    ApiConfig.setBaseUrl('https://engine.example.ru');

    expect(ApiConfig.baseUrl, 'https://engine.example.ru');
    expect(ApiConfig.isOverridden, isTrue);
  });

  test('пустая строка возвращает к адресу из сборки', () {
    ApiConfig.setBaseUrl('https://engine.example.ru');
    ApiConfig.setBaseUrl('   ');

    expect(ApiConfig.isOverridden, isFalse);
    expect(ApiConfig.baseUrl, ApiConfig.compiledBaseUrl);
  });

  test('клиент читает адрес на каждом запросе, а не в конструкторе', () {
    // Клиент живёт всё время работы приложения, а адрес меняется из
    // «Подключений». Запомни его один раз — и после смены запросы уходили бы
    // на старый сервер до перезапуска.
    final client = ApiClient(deviceToken: '');
    expect(client.baseUrl, '');

    ApiConfig.setBaseUrl('https://engine.example.ru');

    expect(client.baseUrl, 'https://engine.example.ru');
  });

  test('движок считается настроенным по адресу, а не по константе сборки', () async {
    final engine = EngineClient(client: ApiClient(deviceToken: ''));
    expect(engine.isConfigured, isFalse);
    // Без адреса выдача объясняет молчание, а не притворяется пустым днём.
    expect((await engine.today()).unavailableReason, contains('Адрес движка не задан'));

    ApiConfig.setBaseUrl('https://engine.example.ru');

    expect(engine.isConfigured, isTrue);
  });

  test('явный адрес клиента сильнее общего', () {
    // Тестовый клиент и особые случаи не должны зависеть от того, что
    // владелец задал в настройках.
    final pinned = ApiClient(baseUrl: 'https://pinned.example', deviceToken: '');
    ApiConfig.setBaseUrl('https://engine.example.ru');

    expect(pinned.baseUrl, 'https://pinned.example');
  });
}
