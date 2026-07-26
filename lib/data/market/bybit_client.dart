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
  BybitClient({HttpJson? http, this.testnet = false, this.candlesPageSize = 1000})
      : _http = http ?? HttpJson();

  final HttpJson _http;
  final bool testnet;

  /// Сколько свечей биржа отдаёт за один запрос — предел Bybit.
  ///
  /// Задаётся явно, как и у [IssClient]: по неполной странице видно, что
  /// история кончилась, а в тестах фикстуры остаются короткими.
  final int candlesPageSize;

  String get _base => testnet ? 'https://api-testnet.bybit.com' : 'https://api.bybit.com';

  /// Тикеры бессрочных контрактов USDT.
  Future<List<BybitTicker>> tickers({List<String>? symbols}) async {
    // Один символ — просим именно его: ответ ~1 КБ вместо ~500 КБ на весь
    // список перпетуалов. Для нескольких символов Bybit фильтра не даёт,
    // поэтому там берём список целиком и фильтруем на устройстве.
    final result = <BybitTicker>[];
    if (symbols != null && symbols.length == 1) {
      result.addAll(await _tickersPage(symbol: symbols.first));
      return result;
    }
    if (symbols != null && symbols.length <= 4) {
      for (final symbol in symbols) {
        result.addAll(await _tickersPage(symbol: symbol));
      }
      return result;
    }
    return _tickersPage(filter: symbols);
  }

  Future<List<BybitTicker>> _tickersPage({String? symbol, List<String>? filter}) async {
    final json = await _http.get(Uri.parse(
      '$_base/v5/market/tickers?category=linear'
      '${symbol == null ? '' : '&symbol=$symbol'}',
    ));
    final symbols = filter;
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
  ///
  /// [from] включает постраничную догрузку: за один запрос биржа отдаёт не
  /// больше 1000 свечей, а бэктесту нужен год часовиков (≈8 800). Страницы
  /// собираются курсором `end` от новых к старым, пока история не дотянется
  /// до [from] или страница не придёт неполной.
  Future<List<Candle>> candles(
    String symbol, {
    required Timeframe timeframe,
    int limit = 200,
    DateTime? from,
  }) async {
    if (from == null) return _candlesPage(symbol, timeframe: timeframe, limit: limit);

    final pageSize = candlesPageSize;
    final collected = <int, Candle>{};
    int? end;
    for (var guard = 0; guard < _maxCandlePages; guard++) {
      final page = await _candlesPage(
        symbol,
        timeframe: timeframe,
        limit: pageSize,
        end: end,
      );
      if (page.isEmpty) break;
      for (final candle in page) {
        collected[candle.time.millisecondsSinceEpoch] = candle;
      }
      final oldest = page.first.time;
      if (!oldest.isAfter(from) || page.length < pageSize) break;
      // Следующая страница — строго старше самой ранней полученной свечи.
      end = oldest.millisecondsSinceEpoch - 1;
    }

    final keys = collected.keys.toList()..sort();
    return [
      for (final key in keys)
        if (!collected[key]!.time.isBefore(from)) collected[key]!,
    ];
  }

  /// Предел страниц — страховка от зацикливания, если курсор не сдвинется.
  static const _maxCandlePages = 12;

  Future<List<Candle>> _candlesPage(
    String symbol, {
    required Timeframe timeframe,
    int limit = 200,
    int? end,
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
          '&interval=$interval&limit=$limit${end == null ? '' : '&end=$end'}'),
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
