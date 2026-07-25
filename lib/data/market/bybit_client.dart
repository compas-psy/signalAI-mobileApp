import '../../domain/analysis/candle.dart';
import 'http_json.dart';

/// Снимок бессрочного контракта Bybit.
class BybitTicker {
  const BybitTicker({
    required this.symbol,
    required this.lastPrice,
    required this.changePercent,
    required this.turnover,
    required this.openInterest,
    required this.fundingRate,
  });

  final String symbol;
  final double lastPrice;
  final double changePercent;
  final double turnover;
  final double openInterest;

  /// Ставка финансирования долей: −0.00008 = −0,008%.
  final double? fundingRate;
}

/// Клиент публичного API Bybit (ТЗ §3.2).
///
/// Рыночные данные отдаются без ключа: тикеры, свечи, открытый интерес,
/// фандинг. Ключи нужны только для баланса и исполнения.
class BybitClient {
  BybitClient({HttpJson? http, this.testnet = false}) : _http = http ?? HttpJson();

  final HttpJson _http;
  final bool testnet;

  String get _base => testnet ? 'https://api-testnet.bybit.com' : 'https://api.bybit.com';

  /// Тикеры бессрочных контрактов USDT.
  Future<List<BybitTicker>> tickers({List<String>? symbols}) async {
    final json = await _http.get(Uri.parse('$_base/v5/market/tickers?category=linear'));
    final list = _list(json);

    final result = <BybitTicker>[];
    for (final item in list) {
      final symbol = item['symbol'] as String?;
      if (symbol == null || !symbol.endsWith('USDT')) continue;
      if (symbols != null && !symbols.contains(symbol)) continue;
      final last = _toDouble(item['lastPrice']);
      if (last == null || last <= 0) continue;

      result.add(
        BybitTicker(
          symbol: symbol,
          lastPrice: last,
          // price24hPcnt приходит долей: 0.0184 = +1,84%.
          changePercent: (_toDouble(item['price24hPcnt']) ?? 0) * 100,
          turnover: _toDouble(item['turnover24h']) ?? 0,
          openInterest: _toDouble(item['openInterest']) ?? 0,
          fundingRate: _toDouble(item['fundingRate']),
        ),
      );
    }
    return result;
  }

  /// Свечи. Bybit отдаёт их от новых к старым — здесь порядок разворачивается.
  Future<List<Candle>> candles(
    String symbol, {
    required Timeframe timeframe,
    int limit = 200,
  }) async {
    // Сетка интервалов Bybit: 1/3/5/15/30/60/120/240/360/720/D/W/M. Десяти
    // минут в ней нет, а тихо отдавать вместо них пятнадцатиминутки нельзя:
    // ISS по тому же Timeframe.m10 вернёт настоящие 10 минут, и два рынка
    // окажутся на разных таймфреймах при одинаковом запросе.
    final interval = switch (timeframe) {
      Timeframe.h1 => '60',
      Timeframe.h4 => '240',
      Timeframe.d1 => 'D',
      Timeframe.m10 => throw ArgumentError(
          'Bybit не отдаёт 10-минутные свечи: используйте Timeframe.h1',
        ),
    };
    final json = await _http.get(
      Uri.parse('$_base/v5/market/kline?category=linear&symbol=$symbol'
          '&interval=$interval&limit=$limit'),
    );

    final rows = (_result(json)['list'] as List<dynamic>? ?? const []);
    final candles = <Candle>[];
    for (final row in rows) {
      final values = (row as List<dynamic>).cast<String>();
      if (values.length < 6) continue;
      final start = int.tryParse(values[0]);
      final close = double.tryParse(values[4]);
      if (start == null || close == null) continue;
      candles.add(
        Candle(
          time: DateTime.fromMillisecondsSinceEpoch(start, isUtc: true),
          open: double.tryParse(values[1]) ?? close,
          high: double.tryParse(values[2]) ?? close,
          low: double.tryParse(values[3]) ?? close,
          close: close,
          volume: double.tryParse(values[5]) ?? 0,
        ),
      );
    }
    return candles.reversed.toList(growable: false);
  }

  /// История открытого интереса — для дельты OI за сутки.
  Future<List<double>> openInterestHistory(String symbol, {int limit = 48}) async {
    final json = await _http.get(
      Uri.parse('$_base/v5/market/open-interest?category=linear&symbol=$symbol'
          '&intervalTime=1h&limit=$limit'),
    );
    final rows = (_result(json)['list'] as List<dynamic>? ?? const []);
    return [
      for (final row in rows.reversed)
        ?_toDouble((row as Map<String, dynamic>)['openInterest']),
    ];
  }

  Map<String, dynamic> _result(Map<String, dynamic> json) {
    final code = json['retCode'];
    if (code != null && code != 0) {
      throw MarketDataException('Bybit: ${json['retMsg'] ?? 'ошибка $code'}');
    }
    return json['result'] as Map<String, dynamic>? ?? const {};
  }

  List<Map<String, dynamic>> _list(Map<String, dynamic> json) =>
      (_result(json)['list'] as List<dynamic>? ?? const []).cast<Map<String, dynamic>>();

  static double? _toDouble(Object? value) => switch (value) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  void close() => _http.close();
}
