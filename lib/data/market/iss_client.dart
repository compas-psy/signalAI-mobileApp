import '../../domain/analysis/candle.dart';
import '../../domain/analysis/instrument_spec.dart';
import '../../domain/enums.dart';
import 'http_json.dart';

/// Снимок торгуемого фьючерса из ISS.
class FortsSnapshot {
  const FortsSnapshot({
    required this.spec,
    required this.lastPrice,
    required this.changePercent,
    required this.turnover,
    required this.openInterest,
    required this.updatedAt,
  });

  final InstrumentSpec spec;
  final double lastPrice;

  /// Изменение к предыдущему закрытию, %.
  final double changePercent;

  /// Оборот за день — по нему отбирается вселенная (ТЗ §3.1).
  final double turnover;
  final double openInterest;

  /// Время последнего обновления данных — для контроля протухания.
  final DateTime? updatedAt;
}

/// Клиент MOEX ISS.
///
/// Публичный API без ключа: свечи, обороты, открытый интерес, параметры
/// контрактов (ТЗ §3.1). Котировки могут отдаваться с задержкой, поэтому для
/// исполнения нужна цена от брокера, а сюда мы ходим за историей и скринером.
class IssClient {
  IssClient({HttpJson? http}) : _http = http ?? HttpJson();

  final HttpJson _http;

  static const _base = 'https://iss.moex.com/iss';
  static const _fortsPath = 'engines/futures/markets/forts';

  /// Снимок срочного рынка целиком.
  Future<List<FortsSnapshot>> fortsSnapshot() async {
    final uri = Uri.parse(
      '$_base/$_fortsPath/securities.json'
      '?iss.meta=off&iss.only=securities,marketdata'
      '&securities.columns=SECID,SHORTNAME,LASTTRADEDATE,MINSTEP,STEPPRICE,DECIMALS,PREVSETTLEPRICE'
      '&marketdata.columns=SECID,LAST,VALTODAY,OPENPOSITION,UPDATETIME',
    );
    final json = await _http.get(uri);

    final marketData = {
      for (final row in issRows(json, 'marketdata')) row['SECID'] as String? ?? '': row,
    };

    final result = <FortsSnapshot>[];
    for (final row in issRows(json, 'securities')) {
      final secId = row['SECID'] as String?;
      if (secId == null) continue;
      final md = marketData[secId];
      final last = _toDouble(md?['LAST']) ?? _toDouble(row['PREVSETTLEPRICE']);
      final minStep = _toDouble(row['MINSTEP']);
      final stepPrice = _toDouble(row['STEPPRICE']);
      if (last == null || last <= 0 || minStep == null || minStep <= 0 || stepPrice == null) {
        continue;
      }
      final previous = _toDouble(row['PREVSETTLEPRICE']);

      result.add(
        FortsSnapshot(
          spec: InstrumentSpec(
            id: secId.toLowerCase(),
            symbol: secId,
            name: (row['SHORTNAME'] as String?) ?? secId,
            market: Market.forts,
            priceDecimals: (_toDouble(row['DECIMALS']) ?? 0).toInt(),
            // Стоимость одного пункта движения цены для одного контракта.
            valuePerPoint: stepPrice / minStep,
            unitMultiplier: 1,
            unitDecimals: 0,
            unitName: 'конт.',
            unitRiskSuffix: 'контракт',
            expiration: DateTime.tryParse((row['LASTTRADEDATE'] as String?) ?? ''),
          ),
          lastPrice: last,
          changePercent:
              previous == null || previous == 0 ? 0 : (last - previous) / previous * 100,
          turnover: _toDouble(md?['VALTODAY']) ?? 0,
          openInterest: _toDouble(md?['OPENPOSITION']) ?? 0,
          updatedAt: _parseUpdateTime(md?['UPDATETIME'] as String?),
        ),
      );
    }
    return result;
  }

  /// Свечи по инструменту. [interval] в минутах: 10, 60, 1440 (день).
  Future<List<Candle>> candles(
    String secId, {
    required Timeframe timeframe,
    required DateTime from,
  }) async {
    final interval = timeframe == Timeframe.d1 ? 24 : timeframe.minutes;
    final uri = Uri.parse(
      '$_base/$_fortsPath/securities/$secId/candles.json'
      '?iss.meta=off&interval=$interval&from=${_date(from)}',
    );
    final json = await _http.get(uri);

    return [
      for (final row in issRows(json, 'candles'))
        if (_toDouble(row['close']) != null)
          Candle(
            time: DateTime.tryParse((row['begin'] as String?) ?? '') ?? DateTime.now(),
            open: _toDouble(row['open'])!,
            high: _toDouble(row['high'])!,
            low: _toDouble(row['low'])!,
            close: _toDouble(row['close'])!,
            volume: _toDouble(row['volume']) ?? 0,
          ),
    ];
  }

  static String _date(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  /// ISS отдаёт UPDATETIME как «ЧЧ:ММ:СС» текущего торгового дня.
  static DateTime? _parseUpdateTime(String? value) {
    if (value == null || value.length < 8) return null;
    final now = DateTime.now();
    final parts = value.split(':');
    if (parts.length < 3) return null;
    final h = int.tryParse(parts[0]);
    final m = int.tryParse(parts[1]);
    final s = int.tryParse(parts[2]);
    if (h == null || m == null || s == null) return null;
    return DateTime(now.year, now.month, now.day, h, m, s);
  }

  static double? _toDouble(Object? value) => switch (value) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  void close() => _http.close();
}
