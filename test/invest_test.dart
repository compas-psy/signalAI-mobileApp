import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/broker/tinvest_fundamentals.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/data/market/http_json.dart';
import 'package:signalai/data/market/iss_client.dart';
import 'package:signalai/domain/analysis/candle.dart';
import 'package:signalai/domain/analysis/instrument_spec.dart';
import 'package:signalai/domain/analysis/screener.dart';
import 'package:signalai/domain/analysis/trade_simulator.dart';
import 'package:signalai/domain/enums.dart';

/// Подставной HTTP: отдаёт заготовки по очереди, как страницы ISS.
class _FakeHttp implements HttpJson {
  _FakeHttp(this._responses);

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

Candle day(int i, double close) => Candle(
      time: DateTime.utc(2026, 1, 5).add(Duration(days: i)),
      open: close - 0.5,
      high: close + 1,
      low: close - 1,
      close: close,
      volume: 1000,
    );

void main() {
  group('Доска акций TQBR', () {
    test('снимок разбирается: спецификация, оборот, лот', () async {
      final iss = IssClient(http: _FakeHttp([
        {
          'securities': {
            'columns': ['SECID', 'SHORTNAME', 'MINSTEP', 'DECIMALS', 'LOTSIZE', 'PREVPRICE'],
            'data': [
              ['SBER', 'Сбербанк', 0.01, 2, 10, 285.5],
              ['GAZP', 'ГАЗПРОМ ао', 0.01, 2, 10, 128.4],
            ],
          },
          'marketdata': {
            'columns': ['SECID', 'LAST', 'VALTODAY'],
            'data': [
              ['SBER', 287.1, 9500000000],
              ['GAZP', 129.0, 4200000000],
            ],
          },
        },
      ]));
      final shares = await iss.sharesSnapshot();

      expect(shares, hasLength(2));
      final sber = shares.firstWhere((s) => s.spec.symbol == 'SBER');
      expect(sber.spec.market, Market.moex);
      expect(sber.spec.tickSize, 0.01);
      expect(sber.lastPrice, 287.1);
      expect(sber.turnover, 9500000000);
      expect(sber.lotSize, 10);
      expect(sber.changePercent, closeTo((287.1 - 285.5) / 285.5 * 100, 1e-9));
    });
  });

  group('Недельный ресемпл', () {
    test('дневки склеиваются по ISO-неделям свеча в свечу', () {
      // Пн 5 янв 2026 — торговые дни без выходных: пн–пт, затем пн–пт.
      final offsets = [0, 1, 2, 3, 4, 7, 8, 9, 10, 11];
      final daily = [
        for (var i = 0; i < offsets.length; i++) day(offsets[i], 100.0 + i),
      ];
      final weeks = resampleWeeks(daily);

      expect(weeks, hasLength(2));
      expect(weeks.first.open, daily.first.open);
      expect(weeks.first.close, daily[4].close, reason: 'пятница закрывает неделю');
      expect(weeks.first.high, daily[4].high);
      expect(weeks.first.low, daily.first.low);
      expect(weeks.last.close, daily.last.close);
    });
  });

  group('Издержки акций', () {
    test('комиссия — доля от цены, фандинга нет', () {
      const spec = InstrumentSpec(
        id: 'sber',
        symbol: 'SBER',
        name: 'Сбербанк',
        market: Market.moex,
        priceDecimals: 2,
        valuePerPoint: 1,
        unitMultiplier: 1,
        unitDecimals: 0,
        unitName: 'акц.',
        unitRiskSuffix: 'акцию',
        tickSize: 0.01,
      );
      final costs = defaultCostsFor(spec);
      expect(costs.feeRate, 0.0005);
      expect(costs.fundingPerBar, 0, reason: 'позиция без плеча — платы за перенос нет');
      expect(costs.slippage, 0.01);
    });
  });

  group('Фундаментальный паспорт', () {
    Future<(HttpServer, TInvestFundamentals)> gateway(
      Map<String, Map<String, dynamic>> replies, {
      String? token = 'TOKEN',
    }) async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        final method = request.uri.path.split('/').last;
        final reply = replies[method];
        if (reply == null) {
          request.response.statusCode = 404;
        } else {
          request.response
            ..headers.contentType = ContentType.json
            ..write(jsonEncode(reply));
        }
        await request.response.close();
      });
      final fundamentals = TInvestFundamentals(
        token: () async => token,
        store: LocalStore.inMemory(),
        baseUrl: 'http://127.0.0.1:${server.port}',
      );
      return (server, fundamentals);
    }

    test('паспорт собирается из трёх сервисов', () async {
      final (server, fundamentals) = await gateway({
        'ShareBy': {
          'instrument': {'uid': 'UID-1', 'assetUid': 'ASSET-1'},
        },
        'GetAssetFundamentals': {
          'fundamentals': [
            {
              'peRatioTtm': 4.2,
              'roe': 24.5,
              'totalDebtToEbitdaMrq': 1.1,
              'dividendYieldDailyTtm': 11.3,
              'marketCapitalization': 6.4e12,
            },
          ],
        },
        'GetForecastBy': {
          'consensus': {
            'recommendation': 'RECOMMENDATION_BUY',
            'consensusPrice': {'units': '340', 'nano': 0},
            'totalBuyRecommend': 9,
            'totalHoldRecommend': 2,
            'totalSellRecommend': 0,
          },
        },
        'GetAssetReports': {
          'events': [
            {
              'reportDate': DateTime.now()
                  .add(const Duration(days: 9))
                  .toIso8601String(),
            },
          ],
        },
      });
      addTearDown(() => server.close(force: true));

      final passport = await fundamentals.passport('SBER');

      expect(passport, isNotNull);
      expect(passport!.peTtm, 4.2);
      expect(passport.roe, 24.5);
      expect(passport.debtToEbitda, 1.1);
      expect(passport.marketCap, 6.4e12);
      expect(passport.consensus, 'RECOMMENDATION_BUY');
      expect(passport.targetPrice, 340);
      expect(passport.buyCount, 9);
      expect(passport.nextReportDate, isNotNull);
      expect(fundamentals.lastError, isNull);
    });

    test('без токена — честный отказ с причиной, а не пустота', () async {
      final (server, fundamentals) = await gateway(const {}, token: null);
      addTearDown(() => server.close(force: true));

      expect(await fundamentals.passport('SBER'), isNull);
      expect(fundamentals.lastError, contains('нет токена'));
    });

    test('отказ прогнозов не валит паспорт целиком', () async {
      final (server, fundamentals) = await gateway({
        'ShareBy': {
          'instrument': {'uid': 'UID-1', 'assetUid': 'ASSET-1'},
        },
        'GetAssetFundamentals': {
          'fundamentals': [
            {'peRatioTtm': 3.1},
          ],
        },
        // GetForecastBy и GetAssetReports отвечают 404.
      });
      addTearDown(() => server.close(force: true));

      final passport = await fundamentals.passport('SBER');
      expect(passport, isNotNull);
      expect(passport!.peTtm, 3.1);
      expect(passport.consensus, isNull);
    });
  });

  group('Дневной скринер', () {
    test('идея по акции получает подпись 1D на графике', () {
      const daySreener = Screener(timeframeLabel: '1D');
      expect(daySreener.timeframeLabel, '1D');
      const swing = Screener();
      expect(swing.timeframeLabel, '1H');
    });
  });
}
