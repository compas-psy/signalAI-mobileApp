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

  /// Время последнего обновления данных в UTC — для контроля протухания.
  ///
  /// null означает, что ISS не отдал `UPDATETIME` (нет торгов по контракту).
  final DateTime? updatedAt;

  /// Возраст котировки относительно [now]. null, если времени обновления нет.
  Duration? ageAt(DateTime now) {
    final at = updatedAt;
    if (at == null) return null;
    return now.toUtc().difference(at);
  }
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
  ///
  /// ISS отдаёт `securities.json` страницами, поэтому блоки собираются по
  /// курсору `start`, иначе на срочном рынке молча теряется хвост контрактов.
  Future<List<FortsSnapshot>> fortsSnapshot() async {
    Uri page(int start) => Uri.parse(
          '$_base/$_fortsPath/securities.json'
          '?iss.meta=off&iss.only=securities,marketdata'
          '&securities.columns=SECID,SHORTNAME,LASTTRADEDATE,MINSTEP,STEPPRICE,DECIMALS,PREVSETTLEPRICE'
          '&marketdata.columns=SECID,LAST,VALTODAY,OPENPOSITION,UPDATETIME'
          '&start=$start',
        );

    final securities = <Map<String, Object?>>[];
    final marketDataRows = <Map<String, Object?>>[];
    for (var start = 0, guard = 0; guard < _maxPages; guard++) {
      final json = await _http.get(page(start));
      final securitiesPage = issRows(json, 'securities');
      final marketDataPage = issRows(json, 'marketdata');
      if (securitiesPage.isEmpty && marketDataPage.isEmpty) break;
      securities.addAll(securitiesPage);
      marketDataRows.addAll(marketDataPage);
      // Курсор двигается по блоку securities — он ведущий в этом запросе.
      if (securitiesPage.isEmpty) break;
      start += securitiesPage.length;
    }

    final marketData = {
      for (final row in marketDataRows) row['SECID'] as String? ?? '': row,
    };

    final now = mskNow();
    final result = <FortsSnapshot>[];
    for (final row in securities) {
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
          updatedAt: parseUpdateTime(md?['UPDATETIME'] as String?, now: now),
        ),
      );
    }
    return result;
  }

  /// Свечи по инструменту.
  ///
  /// Код интервала у ISS — не всегда минуты: день кодируется числом 24, а не
  /// 1440. Ответ страничный (около 500 свечей), поэтому страницы собираются по
  /// курсору `start` — без этого история молча обрезается.
  Future<List<Candle>> candles(
    String secId, {
    required Timeframe timeframe,
    required DateTime from,
  }) async {
    final interval = _issInterval(timeframe);
    Uri page(int start) => Uri.parse(
          '$_base/$_fortsPath/securities/$secId/candles.json'
          '?iss.meta=off&interval=$interval&from=${_date(from)}&start=$start',
        );

    final candles = <Candle>[];
    for (var start = 0, guard = 0; guard < _maxPages; guard++) {
      final rows = issRows(await _http.get(page(start)), 'candles');
      if (rows.isEmpty) break;
      for (final row in rows) {
        final close = _toDouble(row['close']);
        final begin = DateTime.tryParse((row['begin'] as String?) ?? '');
        // Свеча без цены или без времени бесполезна для анализа — пропускаем,
        // подставлять «сейчас» вместо неразобранной даты нельзя.
        if (close == null || begin == null) continue;
        candles.add(
          Candle(
            time: begin,
            open: _toDouble(row['open']) ?? close,
            high: _toDouble(row['high']) ?? close,
            low: _toDouble(row['low']) ?? close,
            close: close,
            volume: _toDouble(row['volume']) ?? 0,
          ),
        );
      }
      start += rows.length;
    }
    return candles;
  }

  /// Код интервала ISS: минуты для внутридневных, 24 — дневная свеча.
  ///
  /// Четырёхчасовых свечей у ISS нет. Молча подменять их часовыми нельзя —
  /// анализ получит не тот таймфрейм, о котором просил, поэтому здесь отказ.
  static int _issInterval(Timeframe timeframe) => switch (timeframe) {
        Timeframe.m10 => 10,
        Timeframe.h1 => 60,
        Timeframe.d1 => 24,
        Timeframe.h4 => throw ArgumentError(
            'MOEX ISS не отдаёт 4-часовые свечи: агрегируйте из часовых',
          ),
      };

  static String _date(DateTime d) =>
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';

  /// Текущее московское время как «настенные часы» биржи.
  static DateTime mskNow() => DateTime.now().toUtc().add(_mskOffset);

  /// Смещение московского времени. С 2014 года фиксированное, перевода нет.
  static const _mskOffset = Duration(hours: 3);

  /// Предел страниц — страховка от зацикливания, если ISS проигнорирует `start`.
  static const _maxPages = 40;

  /// ISS отдаёт UPDATETIME как «ЧЧ:ММ:СС» по Москве, без даты.
  ///
  /// Возвращает момент в UTC: часы биржи склеиваются с московской датой, а не с
  /// датой устройства — иначе у телефона в другом поясе возраст котировки
  /// уезжает на часы. [now] — текущее московское время.
  static DateTime? parseUpdateTime(String? value, {required DateTime now}) {
    if (value == null) return null;
    final parts = value.split(':');
    if (parts.length < 3) return null;
    final h = int.tryParse(parts[0]);
    final m = int.tryParse(parts[1]);
    final s = int.tryParse(parts[2]);
    if (h == null || m == null || s == null) return null;

    // Собираем момент в UTC: московские часы минус смещение.
    var utc = DateTime.utc(now.year, now.month, now.day, h, m, s).subtract(_mskOffset);
    // Около полуночи по Москве метка может относиться к прошедшим суткам.
    if (utc.difference(now.subtract(_mskOffset)) > const Duration(hours: 12)) {
      utc = utc.subtract(const Duration(days: 1));
    }
    return utc;
  }

  static double? _toDouble(Object? value) => switch (value) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  void close() => _http.close();
}
