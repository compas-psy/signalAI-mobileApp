import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/api/api_client.dart';
import 'package:signalai/data/api/engine_client.dart';

/// Подставной клиент: отдаёт заранее заданный ответ вместо сети.
class _FakeApi extends ApiClient {
  _FakeApi(this.rows) : super(baseUrl: 'https://engine.test', deviceToken: '');

  final List<dynamic> rows;

  @override
  Future<List<dynamic>> getList(String path) async {
    lastPath = path;
    return rows;
  }

  String? lastPath;
}

/// Свечи графика идеи (§23, `GET /api/v1/market/{id}/bars`).
///
/// Путь новый: до него график разбора не наполнялся ничем — идеи приходят с
/// движка, а свечи умел строить только скринер на устройстве, и разбор всегда
/// показывал заглушку.
void main() {
  test('цены разбираются из строк, а не из чисел', () async {
    // Сервер отдаёт деньги строками намеренно: JSON-число это double, и шаг
    // цены 0,005 на другом конце становится 0,004999999999999999.
    final api = _FakeApi([
      {
        'open_time': '2026-07-29T10:00:00Z',
        'open': '91240.5',
        'high': '91460.0',
        'low': '91180.5',
        'close': '91420.0',
        'is_closed': true,
      },
      {
        'open_time': '2026-07-29T11:00:00Z',
        'open': '91420.0',
        'high': '91600.0',
        'low': '91400.0',
        'close': '91580.0',
        'is_closed': true,
      },
    ]);

    final chart = await EngineClient(client: api)
        .bars('MOEX:FUT:SIU6', timeframe: '1h');

    expect(chart, isNotNull);
    expect(chart!.candles, hasLength(2));
    expect(chart.candles.first.open, 91240.5);
    expect(chart.candles.first.close, 91420.0);
    expect(chart.timeframeLabel, '1h');
    // Время обязательно: разметка §10.6 привязана к барам, и метка на чужой
    // свече хуже отсутствующей — она выглядит как факт.
    expect(chart.candles.first.openTime, isNotNull);
    expect(api.lastPath, contains('/market/MOEX:FUT:SIU6/bars'));
    expect(api.lastPath, contains('timeframe=1h'));
  });

  test('свеча без полной цены отбрасывается, а не дорисовывается нулём',
      () async {
    final api = _FakeApi([
      {'open_time': '2026-07-29T10:00:00Z', 'open': '100', 'high': '101'},
      {
        'open_time': '2026-07-29T11:00:00Z',
        'open': '100',
        'high': '101',
        'low': '99',
        'close': '100.5',
      },
    ]);

    final chart = await EngineClient(client: api).bars('X', timeframe: '1h');

    // Ноль вместо цены рисуется как обвал, которого не было.
    expect(chart!.candles, hasLength(1));
    expect(chart.candles.single.close, 100.5);
  });

  test('без адреса движка свечи не запрашиваются вовсе', () async {
    final api = _FakeApi(const []);
    // Адрес пуст — клиент обязан не ходить в сеть и вернуть null.
    final chart = await EngineClient(client: ApiClient(baseUrl: '', deviceToken: ''))
        .bars('X', timeframe: '1h');

    expect(chart, isNull);
    expect(api.lastPath, isNull);
  });

  test('пустая выдача не превращается в пустой график', () async {
    final chart =
        await EngineClient(client: _FakeApi(const [])).bars('X', timeframe: '1h');

    // null, а не SignalChart без свечей: виджет обязан показать причину, а не
    // пустую коробку.
    expect(chart, isNull);
  });
}
