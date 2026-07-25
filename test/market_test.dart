import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/market/bybit_client.dart';
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
    test('собирает страницы по курсору start, пока ISS их отдаёт', () async {
      final http = FakeHttp([
        issCandles([
          issCandleRow('2025-07-14 10:00:00', 100),
          issCandleRow('2025-07-14 11:00:00', 101),
        ]),
        issCandles([issCandleRow('2025-07-14 12:00:00', 102)]),
        issCandles(const []),
      ]);
      final client = IssClient(http: http);

      final candles = await client.candles(
        'SiU5',
        timeframe: Timeframe.h1,
        from: DateTime.utc(2025, 7, 14),
      );

      expect(candles.length, 3);
      expect(candles.last.close, 102);
      expect(http.requested[0].queryParameters['start'], '0');
      expect(http.requested[1].queryParameters['start'], '2');
      expect(http.requested[2].queryParameters['start'], '3');
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
}
