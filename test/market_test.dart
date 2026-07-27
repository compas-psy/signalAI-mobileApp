import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/market/bybit_client.dart';
import 'package:signalai/data/market/candle_store.dart';
import 'package:signalai/data/market/http_json.dart';
import 'package:signalai/data/market/iss_client.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/enums.dart';

/// Подставной HTTP: отдаёт заранее заготовленные ответы по порядку запросов.
class FakeHttp implements HttpJson {
  FakeHttp(this._responses);

  final List<Map<String, dynamic>> _responses;
  final List<Uri> requested = [];

  @override
  Duration get timeout => const Duration(seconds: 1);

  @override
  Future<Map<String, dynamic>> get(Uri uri, {int attempts = 3}) async {
    requested.add(uri);
    if (requested.length > _responses.length) return const {};
    return _responses[requested.length - 1];
  }

  @override
  void close() {}
}

/// HTTP, который всегда отказывает: отказ сети — это не «данных нет».
class ThrowingHttp implements HttpJson {
  ThrowingHttp(this.message);

  final String message;

  @override
  Duration get timeout => const Duration(seconds: 1);

  @override
  Future<Map<String, dynamic>> get(Uri uri, {int attempts = 3}) async =>
      throw MarketDataException(message);

  @override
  void close() {}
}

Map<String, dynamic> issCandles(List<List<Object>> rows) => {
      'candles': {
        'columns': ['open', 'close', 'high', 'low', 'value', 'volume', 'begin', 'end'],
        'data': rows,
      },
    };

List<Object> issCandleRow(String begin, double close) =>
    [close, close, close, close, 0, 100, begin, begin];

const sampleSpec = InstrumentSpec(
  id: 'siu5',
  symbol: 'SiU5',
  name: 'Si-9.25',
  market: Market.forts,
  priceDecimals: 0,
  valuePerPoint: 1,
  unitMultiplier: 1,
  unitDecimals: 0,
  unitName: 'конт.',
  unitRiskSuffix: 'контракт',
);

void main() {
  group('IssClient.parseUpdateTime', () {
    test('склеивает московские часы с московской датой, а не с датой устройства', () {
      // 15 июля 2025, 12:30:15 по Москве — это 09:30:15 UTC.
      final now = DateTime.utc(2025, 7, 15, 18, 0);
      final parsed = IssClient.parseUpdateTime('12:30:15', now: now);

      expect(parsed, isNotNull);
      expect(parsed!.isUtc, isTrue);
      expect(parsed, DateTime.utc(2025, 7, 15, 9, 30, 15));
    });

    test('метка около полуночи относится к прошедшим суткам', () {
      // Московское «сейчас» — 00:20 16 июля, метка 23:50 — это вчерашние сутки.
      final now = DateTime.utc(2025, 7, 16, 0, 20);
      final parsed = IssClient.parseUpdateTime('23:50:00', now: now);

      expect(parsed, DateTime.utc(2025, 7, 15, 20, 50));
    });

    test('мусор и пустое значение дают null, а не время устройства', () {
      final now = DateTime.utc(2025, 7, 15, 18, 0);
      expect(IssClient.parseUpdateTime(null, now: now), isNull);
      expect(IssClient.parseUpdateTime('', now: now), isNull);
      expect(IssClient.parseUpdateTime('12:30', now: now), isNull);
      expect(IssClient.parseUpdateTime('чч:мм:сс', now: now), isNull);
    });
  });

  group('IssClient.candles', () {
    test('собирает страницы по курсору start, пока страницы полные', () async {
      // Полная страница (2 строки) → идём дальше; неполная (1 строка) —
      // последняя, и холостой запрос за пустой страницей больше не делается:
      // на 16 вызовах candles() за пересчёт это 16 лишних round-trip'ов.
      final http = FakeHttp([
        issCandles([
          issCandleRow('2025-07-14 10:00:00', 100),
          issCandleRow('2025-07-14 11:00:00', 101),
        ]),
        issCandles([issCandleRow('2025-07-14 12:00:00', 102)]),
      ]);
      final client = IssClient(http: http, candlesPageSize: 2);

      final candles = await client.candles(
        'SiU5',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(candles.length, 3);
      expect(candles.last.close, 102);
      expect(http.requested, hasLength(2), reason: 'третий запрос был холостым');
      expect(http.requested[0].queryParameters['start'], '0');
      expect(http.requested[1].queryParameters['start'], '2');
    });

    test('история длиннее одной страницы дочитывается до конца', () async {
      // Три полные страницы подряд — курсор обязан двигаться, иначе история
      // молча обрезается.
      final full = issCandles([
        issCandleRow('2025-07-14 10:00:00', 100),
        issCandleRow('2025-07-14 11:00:00', 101),
      ]);
      final http = FakeHttp([
        full,
        full,
        issCandles([issCandleRow('2025-07-14 12:00:00', 102)]),
      ]);

      final candles = await IssClient(http: http, candlesPageSize: 2).candles(
        'SiU5',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(candles.length, 5);
      expect(http.requested, hasLength(3));
      expect(http.requested[2].queryParameters['start'], '4');
    });

    test('запрашивается только блок candles', () async {
      final http = FakeHttp([issCandles([issCandleRow('2025-07-14 10:00:00', 100)])]);

      await IssClient(http: http, candlesPageSize: 2).candles(
        'SiU5',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(http.requested.single.query, contains('iss.only=candles'));
    });

    test('дневная свеча запрашивается интервалом 24, а не 1440', () async {
      final http = FakeHttp([issCandles(const [])]);

      await IssClient(http: http).candles(
        'SiU5',
        timeframe: Timeframe.d1,
        from: DateTime.utc(2025, 7, 1),
      );

      expect(http.requested.single.queryParameters['interval'], '24');
    });

    test('свеча без разобранной даты отбрасывается, а не датируется «сейчас»', () async {
      final http = FakeHttp([
        issCandles([
          issCandleRow('не дата', 100),
          issCandleRow('2025-07-14 11:00:00', 101),
        ]),
        issCandles(const []),
      ]);

      final candles = await IssClient(http: http).candles(
        'SiU5',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(candles.single.close, 101);
    });

    test('четырёхчасовой таймфрейм отклоняется, а не подменяется часовым', () {
      final future = IssClient(http: FakeHttp(const [])).candles(
        'SiU5',
        timeframe: Timeframe.h4,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(future, throwsArgumentError);
    });
  });

  group('IssClient.previousSeries', () {
    test('квартальный контракт разворачивается назад по кварталам', () {
      expect(
        IssClient.previousSeries('SiZ5', count: 4),
        ['SiZ5', 'SiU5', 'SiM5', 'SiH5'],
      );
    });

    test('переход через год уменьшает цифру года', () {
      expect(
        IssClient.previousSeries('RIH6', count: 3),
        ['RIH6', 'RIZ5', 'RIU5'],
      );
    });

    test('нефть исполняется ежемесячно, а не поквартально', () {
      expect(IssClient.isMonthlySeries('BRZ5'), isTrue);
      expect(IssClient.isMonthlySeries('SiZ5'), isFalse);
      expect(
        IssClient.previousSeries('BRG6', count: 3),
        ['BRG6', 'BRF6', 'BRZ5'],
      );
    });

    test('нераспознанный символ возвращается как есть, а не выдумывается', () {
      expect(IssClient.previousSeries('XX'), ['XX']);
      expect(IssClient.previousSeries('SiQ5'), ['SiQ5'],
          reason: 'август — не квартальный месяц исполнения');
    });
  });

  group('IssClient инкрементальная история', () {
    test('вторая загрузка тянет хвост, а не всю историю заново', () async {
      final http = FakeHttp([
        issCandles([
          issCandleRow('2025-07-14 10:00:00', 100),
          issCandleRow('2025-07-15 10:00:00', 101),
        ]),
        issCandles([issCandleRow('2025-07-16 10:00:00', 102)]),
      ]);
      final client = IssClient(http: http, candlesPageSize: 5)
        ..store = CandleStore(LocalStore.inMemory());

      Future<List<Candle>> load() => client.candles(
            'SiU5',
            timeframe: Timeframe.d1,
            from: DateTime.utc(2025, 7, 1),
            cache: true,
          );

      final first = await load();
      expect(first, hasLength(2));

      final second = await load();
      // Известные свечи никуда не делись, новая добавилась.
      expect(second.map((c) => c.close), [100, 101, 102]);
      expect(http.requested, hasLength(2));
      expect(
        http.requested[0].queryParameters['from'],
        '2025-07-01',
        reason: 'первый раз истории нет — качаем с запрошенной даты',
      );
      expect(
        http.requested[1].queryParameters['from'],
        '2025-07-15',
        reason: 'второй раз догружаем от последней известной свечи',
      );
    });

    test('без хранилища поведение прежнее — полная загрузка', () async {
      final http = FakeHttp([
        issCandles([issCandleRow('2025-07-14 10:00:00', 100)]),
        issCandles([issCandleRow('2025-07-14 10:00:00', 100)]),
      ]);
      final client = IssClient(http: http, candlesPageSize: 5);

      Future<void> load() => client.candles(
            'SiU5',
            timeframe: Timeframe.h1,
            from: DateTime.utc(2025, 7, 14),
            cache: true,
          );

      await load();
      await load();
      expect(http.requested, hasLength(2));
      expect(http.requested[1].queryParameters['from'], '2025-07-14');
    });
  });

  group('BybitClient', () {
    test('десятиминутный таймфрейм отклоняется, а не подменяется 15-минутным', () {
      final future = BybitClient(http: FakeHttp(const [])).candles(
        'BTCUSDT',
        timeframe: Timeframe.m10,
      );

      expect(future, throwsArgumentError);
    });

    test('свечи разворачиваются от старых к новым', () async {
      final http = FakeHttp([
        {
          'retCode': 0,
          'result': {
            'list': [
              ['1752580800000', '2', '2', '2', '2', '10', '20'],
              ['1752577200000', '1', '1', '1', '1', '10', '20'],
            ],
          },
        },
      ]);

      final candles = await BybitClient(http: http).candles(
        'BTCUSDT',
        timeframe: Timeframe.h1,
      );

      expect(candles.first.close, 1);
      expect(candles.last.close, 2);
      expect(candles.first.time.isBefore(candles.last.time), isTrue);
    });

    test('история глубже страницы дочитывается курсором end', () async {
      // Первая страница — новые свечи, вторая — более старые. Курсор обязан
      // сдвинуться, иначе год истории превратится в 1000 свечей.
      Map<String, dynamic> page(List<int> hours) => {
            'retCode': 0,
            'result': {
              'list': [
                for (final h in hours.reversed)
                  ['${DateTime.utc(2025, 7, 14, h).millisecondsSinceEpoch}',
                   '1', '1', '1', '1', '10', '20'],
              ],
            },
          };

      final http = FakeHttp([
        page([12, 13]),
        page([10, 11]),
      ]);

      final candles = await BybitClient(http: http, candlesPageSize: 2).candles(
        'BTCUSDT',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14, 10),
      );

      expect(candles, hasLength(4));
      expect(candles.first.time, DateTime.utc(2025, 7, 14, 10));
      expect(candles.last.time, DateTime.utc(2025, 7, 14, 13));
      expect(http.requested[0].queryParameters['end'], isNull);
      expect(
        http.requested[1].queryParameters['end'],
        '${DateTime.utc(2025, 7, 14, 12).millisecondsSinceEpoch - 1}',
        reason: 'вторая страница обязана быть строго старше первой',
      );
    });

    test('свечи старше запрошенной границы отбрасываются', () async {
      final http = FakeHttp([
        {
          'retCode': 0,
          'result': {
            'list': [
              ['${DateTime.utc(2025, 7, 14, 12).millisecondsSinceEpoch}',
               '1', '1', '1', '1', '10', '20'],
              ['${DateTime.utc(2025, 7, 10, 12).millisecondsSinceEpoch}',
               '1', '1', '1', '1', '10', '20'],
            ],
          },
        },
      ]);

      final candles = await BybitClient(http: http).candles(
        'BTCUSDT',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(candles, hasLength(1));
      expect(candles.single.time, DateTime.utc(2025, 7, 14, 12));
    });

    test('ненулевой retCode поднимается как ошибка рыночных данных', () {
      final http = FakeHttp([
        {'retCode': 10001, 'retMsg': 'params error'},
      ]);

      expect(
        BybitClient(http: http).tickers(),
        throwsA(isA<MarketDataException>()),
      );
    });
  });

  group('FortsSnapshot.ageAt', () {
    test('возраст котировки считается от времени обновления в UTC', () {
      final snapshot = FortsSnapshot(
        spec: sampleSpec,
        lastPrice: 100,
        changePercent: 0,
        turnover: 0,
        openInterest: 0,
        updatedAt: DateTime.utc(2025, 7, 15, 9, 30),
      );

      expect(
        snapshot.ageAt(DateTime.utc(2025, 7, 15, 10, 0)),
        const Duration(minutes: 30),
      );
    });

    test('без времени обновления возраст неизвестен', () {
      final snapshot = FortsSnapshot(
        spec: sampleSpec,
        lastPrice: 100,
        changePercent: 0,
        turnover: 0,
        openInterest: 0,
        updatedAt: null,
      );

      expect(snapshot.ageAt(DateTime.utc(2025, 7, 15, 10, 0)), isNull);
    });
  });

  group('IssClient.optionsChain', () {
    test('первый запрос идёт с фильтром по базовому активу', () async {
      // Доска опционов MOEX — десятки тысяч контрактов. Полный обход по сто
      // строк до нужного актива не доходит, а сорок последовательных запросов
      // вешают экран. Ровно это и увидел владелец: раздел пустой, диагностика
      // стоит на этой же строке.
      final http = FakeHttp([issOptions(const [])]);
      await IssClient(http: http).optionsChain('SI', ttl: Duration.zero);

      expect(http.requested.first.query, contains('assetcode=SI'));
    });

    test('колонки запрашиваются поимённо', () async {
      // Без этого биржа отдаёт все ~40 колонок доски, и на мобильной сети
      // тело ответа не успевает прийти: соединение рвётся на середине, а
      // выглядит это как «биржа не ответила». Владелец получил ровно такой
      // ответ на экране сырых данных: 11,5 с и обрыв.
      final http = FakeHttp([issOptions(const [])]);
      await IssClient(http: http).optionsChain('SI', ttl: Duration.zero);

      final query = http.requested.first.query;
      expect(query, contains('securities.columns=SECID,ASSETCODE,STRIKE'));
      expect(query, contains('marketdata.columns=SECID,LAST,THEORPRICE'));
    });

    test('доска не качается по второму разу, если limit не соблюдён', () async {
      // Страница длиннее запрошенного limit означает, что пришла вся доска.
      // Раньше это выяснялось только после повторной полной закачки —
      // дедупликацией по SECID.
      final board = [
        for (var i = 0; i < 150; i++)
          issOptionRow('SI${90000 + i}BC5', 'SI', 90000 + i.toDouble(), 'C'),
      ];
      final http = FakeHttp([issOptions(board), issOptions(board)]);
      final result = await IssClient(http: http)
          .optionsChainProbe('SI', ttl: Duration.zero);

      expect(http.requested.length, 1, reason: 'вторая закачка бессмысленна');
      expect(result.rows.length, 150);
      expect(result.reports.single.limitIgnored, isTrue);
    });

    test('фильтрованный ответ разбирается без обхода всей доски', () async {
      final http = FakeHttp([
        issOptions([
          issOptionRow('SI95000BC5', 'SI', 95000, 'C'),
          issOptionRow('SI95000BX5', 'SI', 95000, 'P'),
        ]),
      ]);
      final result = await IssClient(http: http)
          .optionsChainProbe('SI', ttl: Duration.zero);

      expect(result.rows.length, 2);
      expect(http.requested.length, 1, reason: 'одна страница — один запрос');
      expect(result.note, contains('фильтр'));
    });

    test('если фильтр не поддержан, идёт обход доски', () async {
      // Первый ответ пуст — биржа фильтр проигнорировала или не знает его.
      // Молча вернуть «цепочки нет» нельзя: данные на доске есть.
      final http = FakeHttp([
        issOptions(const []),
        issOptions([issOptionRow('SI95000BC5', 'SI', 95000, 'C')]),
      ]);
      final result = await IssClient(http: http)
          .optionsChainProbe('SI', ttl: Duration.zero);

      expect(result.rows.length, 1);
      expect(result.note, contains('обход'));
      expect(http.requested.length, 2);
      expect(http.requested.last.query, isNot(contains('assetcode')));
    });

    test('чужой актив в цепочку не попадает', () async {
      final http = FakeHttp([
        issOptions([
          issOptionRow('SI95000BC5', 'SI', 95000, 'C'),
          issOptionRow('RI11000BC5', 'RI', 110000, 'C'),
        ]),
      ]);
      final rows = await IssClient(http: http).optionsChain('SI', ttl: Duration.zero);

      expect(rows.length, 1);
      expect(rows.single.assetCode, 'SI');
    });

    test('пустая выдача не запирает раздел кэшем', () async {
      // Кэш пустого результата означал бы: одна неудачная попытка — и десять
      // минут раздел не работает, сколько кнопку ни жми.
      final http = FakeHttp([
        issOptions(const []),
        issOptions(const []),
        issOptions([issOptionRow('SI95000BC5', 'SI', 95000, 'C')]),
      ]);
      final client = IssClient(http: http);

      expect(await client.optionsChain('SI'), isEmpty);
      expect(await client.optionsChain('SI'), isNotEmpty,
          reason: 'повторный запрос обязан снова пойти на биржу');
    });

    test('протокол запроса называет колонки, которые пришли', () async {
      // То, чего не хватило при разработке: состав выдачи ISS меняется, а
      // проверить его из среды сборки нечем.
      final http = FakeHttp([issOptions(const [])]);
      final result = await IssClient(http: http)
          .optionsChainProbe('SI', ttl: Duration.zero);

      final shape = result.reports.first.blocks['securities']!;
      expect(shape.present, isTrue);
      expect(shape.columns, contains('STRIKE'));
      expect(shape.columns, contains('ASSETCODE'));
    });

    test('отказ биржи виден в протоколе, а не превращается в «данных нет»',
        () async {
      final result = await IssClient(http: ThrowingHttp('сеть недоступна'))
          .optionsChainProbe('SI', ttl: Duration.zero);

      expect(result.rows, isEmpty);
      expect(result.failure, contains('сеть недоступна'));
    });
  });
}

/// Ответ доски опционов: колонки те же, что отдаёт ISS.
Map<String, dynamic> issOptions(List<List<Object?>> securities) => {
      'securities': {
        'columns': [
          'SECID',
          'ASSETCODE',
          'STRIKE',
          'OPTIONTYPE',
          'LASTTRADEDATE',
          'MINSTEP',
          'DECIMALS',
        ],
        'data': securities,
      },
      'marketdata': {
        'columns': ['SECID', 'LAST', 'THEORPRICE', 'IMPLIEDVOLATILITY'],
        'data': [
          for (final row in securities) [row[0], 500, 505, 22.5],
        ],
      },
    };

List<Object?> issOptionRow(String secId, String asset, double strike, String type) =>
    [secId, asset, strike, type, '2026-09-17', 1, 0];
