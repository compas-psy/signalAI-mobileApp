import 'dart:convert';
import 'dart:io';

import '../../domain/broker/broker.dart';
import '../../domain/invest/invest_models.dart';
import '../local_store.dart';
import '../net/resilient_http.dart';

/// Фундаментальные данные акций из Invest API Т-Инвестиций.
///
/// Тот же REST-шлюз и тот же токен, что у торгового адаптера: справочные
/// сервисы (Instruments) общие для песочницы и живого счёта. API отдаёт
/// только текущий срез показателей — истории нет, поэтому паспорт по
/// построению справочный: он не участвует в ранжире и не проверяется
/// бэктестом, и это написано в самой карточке.
class TInvestFundamentals {
  TInvestFundamentals({
    required Future<String?> Function() token,
    required LocalStore store,
    HttpClient? client,
    this.baseUrl,
    this.timeout = const Duration(seconds: 20),
    this.cacheTtl = const Duration(hours: 24),
  })  : _token = token,
        _store = store,
        _client = client ?? resilientHttpClient();

  final Future<String?> Function() _token;
  final LocalStore _store;
  final HttpClient _client;
  final Duration timeout;

  /// Паспорт живёт сутки: раздел пересчитывается ночью, чаще дёргать API
  /// незачем — отчётность не меняется по часам.
  final Duration cacheTtl;

  final String? baseUrl;

  String get _base => baseUrl ?? 'https://invest-public-api.tinkoff.ru/rest';
  static const _ns = 'tinkoff.public.invest.api.contract.v1';

  static const _cacheFile = 'invest_fundamentals';

  /// Паспорт по тикеру TQBR. null — токена нет либо API не ответил;
  /// причина возвращается через [lastError].
  Future<FundamentalsPassport?> passport(String ticker) async {
    final cached = await _cached(ticker);
    if (cached != null) return cached;

    final token = await _token();
    if (token == null || token.isEmpty) {
      lastError = 'нет токена Т-Инвестиций — введите его в настройках, '
          'паспорта появятся при следующем пересчёте';
      return null;
    }

    try {
      final share = await _share(ticker, token);
      final uid = share['uid'] as String?;
      final assetUid = share['assetUid'] as String?;
      if (uid == null || assetUid == null) {
        lastError = 'Т-Инвестиции не знают бумагу $ticker';
        return null;
      }

      final fundamentals = await _fundamentals(assetUid, token);
      final forecast = await _forecast(uid, token);
      final report = await _nextReport(assetUid, token);

      final passport = _build(fundamentals, forecast, report);
      await _remember(ticker, passport);
      lastError = null;
      return passport;
    } on BrokerException catch (e) {
      lastError = e.message;
      return null;
    } on Object catch (e) {
      lastError = 'нет связи с Invest API: $e';
      return null;
    }
  }

  /// Причина последнего отказа — для честной строки в разделе.
  String? lastError;

  Future<Map<String, dynamic>> _share(String ticker, String token) async {
    final json = await _call('InstrumentsService', 'ShareBy', token, {
      'idType': 'INSTRUMENT_ID_TYPE_TICKER',
      'classCode': 'TQBR',
      'id': ticker,
    });
    return json['instrument'] as Map<String, dynamic>? ?? const {};
  }

  Future<Map<String, dynamic>> _fundamentals(String assetUid, String token) async {
    final json = await _call('InstrumentsService', 'GetAssetFundamentals', token, {
      'assets': [assetUid],
    });
    final list = json['fundamentals'] as List<dynamic>? ?? const [];
    return list.isEmpty ? const {} : list.first as Map<String, dynamic>;
  }

  Future<Map<String, dynamic>> _forecast(String uid, String token) async {
    try {
      final json = await _call('InstrumentsService', 'GetForecastBy', token, {
        'instrumentId': uid,
      });
      return json['consensus'] as Map<String, dynamic>? ?? const {};
    } on BrokerException {
      // Прогнозов может не быть — это не отказ паспорта целиком.
      return const {};
    }
  }

  Future<DateTime?> _nextReport(String assetUid, String token) async {
    try {
      final now = DateTime.now();
      final json = await _call('InstrumentsService', 'GetAssetReports', token, {
        'instrumentId': assetUid,
        'from': now.toUtc().toIso8601String(),
        'to': now.add(const Duration(days: 120)).toUtc().toIso8601String(),
      });
      final events = json['events'] as List<dynamic>? ?? const [];
      DateTime? nearest;
      for (final event in events) {
        final date = DateTime.tryParse(
            (event as Map<String, dynamic>)['reportDate'] as String? ?? '');
        if (date == null || date.isBefore(now)) continue;
        if (nearest == null || date.isBefore(nearest)) nearest = date;
      }
      return nearest;
    } on BrokerException {
      return null;
    }
  }

  FundamentalsPassport _build(
    Map<String, dynamic> f,
    Map<String, dynamic> consensus,
    DateTime? report,
  ) {
    double? d(Object? v) => switch (v) {
          num n => n.toDouble(),
          String s => double.tryParse(s),
          Map<String, dynamic> q => _quotation(q),
          _ => null,
        };
    return FundamentalsPassport(
      at: DateTime.now(),
      marketCap: d(f['marketCapitalization']),
      peTtm: d(f['peRatioTtm']),
      psTtm: d(f['priceToSalesTtm']),
      pbTtm: d(f['priceToBookTtm']),
      evToEbitda: d(f['evToEbitdaMrq']),
      roe: d(f['roe']),
      netMargin: d(f['netMarginMrq']),
      debtToEbitda: d(f['totalDebtToEbitdaMrq']),
      dividendYieldTtm: d(f['dividendYieldDailyTtm']),
      forwardDividendYield: d(f['forwardAnnualDividendYield']),
      revenueGrowthOneYear: d(f['oneYearAnnualRevenueGrowthRate']),
      revenueGrowthFiveYears: d(f['fiveYearAnnualRevenueGrowthRate']),
      freeFloat: d(f['freeFloat']),
      consensus: consensus['recommendation'] as String? ??
          consensus['consensus'] as String?,
      targetPrice: d(consensus['consensusPrice'] ?? consensus['bestTargetPrice']),
      buyCount: (consensus['totalBuyRecommend'] as num?)?.toInt(),
      holdCount: (consensus['totalHoldRecommend'] as num?)?.toInt(),
      sellCount: (consensus['totalSellRecommend'] as num?)?.toInt(),
      nextReportDate: report,
    );
  }

  static double? _quotation(Map<String, dynamic> q) {
    final units = q['units'];
    final nano = q['nano'];
    final u = switch (units) {
      num n => n.toDouble(),
      String s => double.tryParse(s),
      _ => null,
    };
    if (u == null) return null;
    final n = switch (nano) { num v => v.toDouble(), _ => 0.0 };
    return u + n / 1e9;
  }

  // ── Кэш на диске: сутки на тикер ────────────────────────────────────────

  Future<FundamentalsPassport?> _cached(String ticker) async {
    final all = await _store.read(_cacheFile) ?? const {};
    final passport =
        FundamentalsPassport.fromJson(all[ticker] as Map<String, dynamic>?);
    if (passport == null) return null;
    if (DateTime.now().difference(passport.at) > cacheTtl) return null;
    return passport;
  }

  Future<void> _remember(String ticker, FundamentalsPassport passport) async {
    final all = Map<String, dynamic>.from(await _store.read(_cacheFile) ?? {});
    all[ticker] = passport.toJson();
    await _store.write(_cacheFile, all);
  }

  Future<Map<String, dynamic>> _call(
    String service,
    String method,
    String token,
    Map<String, dynamic> body,
  ) async {
    try {
      final uri = Uri.parse('$_base/$_ns.$service/$method');
      final request = await _client.postUrl(uri).timeout(timeout);
      request.headers
        ..set(HttpHeaders.authorizationHeader, 'Bearer $token')
        ..set(HttpHeaders.contentTypeHeader, 'application/json');
      request.write(jsonEncode(body));
      final response = await request.close().timeout(timeout);
      final text = await response.transform(utf8.decoder).join().timeout(timeout);
      if (response.statusCode >= 400) {
        String reason = 'HTTP ${response.statusCode}';
        try {
          final decoded = jsonDecode(text);
          if (decoded is Map<String, dynamic>) {
            reason = '$reason · ${decoded['message'] ?? text}';
          }
        } on FormatException {
          // Не JSON — оставим код.
        }
        throw BrokerException('Invest API отказал: $reason');
      }
      final decoded = jsonDecode(text);
      if (decoded is! Map<String, dynamic>) {
        throw const BrokerException('Invest API вернул неожиданный формат');
      }
      return decoded;
    } on BrokerException {
      rethrow;
    } on Object catch (e) {
      throw BrokerException('Нет связи с Invest API: $e');
    }
  }
}
