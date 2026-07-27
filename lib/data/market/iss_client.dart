import '../../domain/analysis/candle.dart';
import '../../domain/analysis/instrument_spec.dart';
import '../../domain/enums.dart';
import 'candle_store.dart';
import 'http_json.dart';

/// Снимок одной акции основной доски: спецификация, цена, оборот, лот.
class ShareSnapshot {
  const ShareSnapshot({
    required this.spec,
    required this.lastPrice,
    required this.changePercent,
    required this.turnover,
    required this.lotSize,
  });

  final InstrumentSpec spec;
  final double lastPrice;
  final double changePercent;

  /// Оборот за день, ₽ — фильтр ликвидности вселенной «Инвест».
  final double turnover;

  /// Акций в лоте заявки.
  final int lotSize;
}

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
  IssClient({HttpJson? http, this.candlesPageSize = 500})
      : _http = http ?? HttpJson();

  /// Сколько свечей ISS отдаёт на одну страницу.
  ///
  /// Значение задаётся явно, а не выводится из первой страницы: если история
  /// целиком уместилась в одну неполную страницу, вывести размер из неё
  /// нельзя, и пришлось бы делать лишний запрос, чтобы это выяснить.
  /// В тестах подменяется, чтобы фикстуры оставались короткими.
  final int candlesPageSize;

  final HttpJson _http;

  static const _base = 'https://iss.moex.com/iss';
  static const _fortsPath = 'engines/futures/markets/forts';

  /// Снимок срочного рынка целиком.
  ///
  /// ISS отдаёт `securities.json` страницами, поэтому блоки собираются по
  /// курсору `start`, иначе на срочном рынке молча теряется хвост контрактов.
  /// Снимок с коротким кэшем: бэктест и оптимизация, запущенные сразу после
  /// дайджеста, тянули весь рынок заново. Спецификации контрактов меняются
  /// раз в квартал, обороты — не чаще минут, поэтому пять минут безопасны.
  Future<List<FortsSnapshot>> fortsSnapshot({Duration ttl = const Duration(minutes: 5)}) async {
    final cached = _snapshotCache;
    final at = _snapshotAt;
    if (cached != null && at != null && DateTime.now().difference(at) < ttl) {
      return cached;
    }
    final fresh = await _fetchSnapshot();
    _snapshotCache = fresh;
    _snapshotAt = DateTime.now();
    return fresh;
  }

  List<FortsSnapshot>? _snapshotCache;
  DateTime? _snapshotAt;

  Future<List<FortsSnapshot>> _fetchSnapshot() async {
    Uri page(int start) => Uri.parse(
          '$_base/$_fortsPath/securities.json'
          '?iss.meta=off&iss.only=securities,marketdata'
          '&securities.columns=SECID,SHORTNAME,LASTTRADEDATE,MINSTEP,STEPPRICE,DECIMALS,PREVSETTLEPRICE'
          '&marketdata.columns=SECID,LAST,VALTODAY,OPENPOSITION,UPDATETIME'
          '&limit=$_snapshotPageSize&start=$start',
        );

    final securities = <Map<String, Object?>>[];
    final marketDataRows = <Map<String, Object?>>[];
    // Дедупликация по SECID: страховка на случай, если ISS проигнорирует
    // курсор и начнёт отдавать ту же страницу — иначе цикл честно отработал
    // бы все 40 запросов и склеил дубликаты.
    final seen = <String>{};
    for (var start = 0, guard = 0; guard < _maxPages; guard++) {
      final json = await _http.get(page(start));
      final securitiesPage = issRows(json, 'securities');
      final marketDataPage = issRows(json, 'marketdata');
      if (securitiesPage.isEmpty && marketDataPage.isEmpty) break;

      var fresh = 0;
      for (final row in securitiesPage) {
        final secId = row['SECID'] as String?;
        if (secId == null || !seen.add(secId)) continue;
        securities.add(row);
        fresh++;
      }
      marketDataRows.addAll(marketDataPage);

      if (securitiesPage.isEmpty || fresh == 0) break;
      // Неполная страница означает, что это последняя: лишний холостой запрос
      // за пустой страницей не нужен.
      if (securitiesPage.length < _snapshotPageSize) break;
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
            // Шаг цены нужен модели исполнения: у RI знаков после запятой ноль,
            // а шаг равен 10 пунктам — вывести его из DECIMALS нельзя.
            tickSize: minStep,
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

  /// Постоянное хранилище истории. null — работаем без него, как раньше.
  CandleStore? store;

  static const _sharesPath = 'engines/stock/markets/shares/boards/TQBR';

  /// Снимок всей основной доски акций (TQBR): «весь рынок» раздела «Инвест».
  ///
  /// Кэш длиннее фьючерсного: раздел пересчитывается раз в сутки, и обороты
  /// нужны только для фильтра ликвидности, а не для исполнения.
  Future<List<ShareSnapshot>> sharesSnapshot({Duration ttl = const Duration(hours: 1)}) async {
    final cached = _sharesCache;
    final at = _sharesAt;
    if (cached != null && at != null && DateTime.now().difference(at) < ttl) {
      return cached;
    }
    final fresh = await _fetchShares();
    _sharesCache = fresh;
    _sharesAt = DateTime.now();
    return fresh;
  }

  List<ShareSnapshot>? _sharesCache;
  DateTime? _sharesAt;

  Future<List<ShareSnapshot>> _fetchShares() async {
    Uri page(int start) => Uri.parse(
          '$_base/$_sharesPath/securities.json'
          '?iss.meta=off&iss.only=securities,marketdata'
          '&securities.columns=SECID,SHORTNAME,MINSTEP,DECIMALS,LOTSIZE,PREVPRICE'
          '&marketdata.columns=SECID,LAST,VALTODAY'
          '&limit=$_snapshotPageSize&start=$start',
        );

    final securities = <Map<String, Object?>>[];
    final marketDataRows = <Map<String, Object?>>[];
    final seen = <String>{};
    for (var start = 0, guard = 0; guard < _maxPages; guard++) {
      final json = await _http.get(page(start));
      final securitiesPage = issRows(json, 'securities');
      final marketDataPage = issRows(json, 'marketdata');
      if (securitiesPage.isEmpty && marketDataPage.isEmpty) break;

      var fresh = 0;
      for (final row in securitiesPage) {
        final secId = row['SECID'] as String?;
        if (secId == null || !seen.add(secId)) continue;
        securities.add(row);
        fresh++;
      }
      marketDataRows.addAll(marketDataPage);
      if (securitiesPage.isEmpty || fresh == 0) break;
      if (securitiesPage.length < _snapshotPageSize) break;
      start += securitiesPage.length;
    }

    final marketData = {
      for (final row in marketDataRows) row['SECID'] as String? ?? '': row,
    };

    final result = <ShareSnapshot>[];
    for (final row in securities) {
      final secId = row['SECID'] as String?;
      if (secId == null) continue;
      final md = marketData[secId];
      final previous = _toDouble(row['PREVPRICE']);
      final last = _toDouble(md?['LAST']) ?? previous;
      final minStep = _toDouble(row['MINSTEP']);
      if (last == null || last <= 0 || minStep == null || minStep <= 0) continue;

      result.add(ShareSnapshot(
        spec: InstrumentSpec(
          id: secId.toLowerCase(),
          symbol: secId,
          name: (row['SHORTNAME'] as String?) ?? secId,
          market: Market.moex,
          priceDecimals: (_toDouble(row['DECIMALS']) ?? 2).toInt(),
          // Акция: пункт цены и есть рубль на одну акцию.
          valuePerPoint: 1,
          unitMultiplier: 1,
          unitDecimals: 0,
          unitName: 'акц.',
          unitRiskSuffix: 'акцию',
          tickSize: minStep,
        ),
        lastPrice: last,
        changePercent:
            previous == null || previous == 0 ? 0 : (last - previous) / previous * 100,
        turnover: _toDouble(md?['VALTODAY']) ?? 0,
        lotSize: (_toDouble(row['LOTSIZE']) ?? 1).toInt(),
      ));
    }
    return result;
  }

  /// Дневные свечи акции с основной доски — инкрементально через хранилище.
  Future<List<Candle>> shareCandles(String secId, {required DateTime from}) async {
    const interval = 24; // код дневки у ISS
    final candleStore = store;
    if (candleStore == null) {
      return _fetchCandles(secId, interval, from, path: _sharesPath);
    }
    final key = CandleStore.keyFor('stock:$secId', '$interval');
    final known = await candleStore.load(key);
    final since = candleStore.incrementalStart(known, from);
    final fresh =
        await _fetchCandles(secId, interval, since ?? from, path: _sharesPath);
    final merged = await candleStore.merge(key, fresh, coveredFrom: from);
    return [for (final c in merged) if (!c.time.isBefore(from)) c];
  }

  /// Свечи по инструменту.
  ///
  /// Код интервала у ISS — не всегда минуты: день кодируется числом 24, а не
  /// 1440. Ответ страничный (около 500 свечей), поэтому страницы собираются по
  /// курсору `start` — без этого история молча обрезается.
  ///
  /// При [cache] история берётся из постоянного хранилища, а с биржи
  /// запрашивается только хвост от последней известной свечи. Для истёкшей
  /// серии это ноль новых свечей и один короткий ответ вместо трёх страниц.
  Future<List<Candle>> candles(
    String secId, {
    required Timeframe timeframe,
    required DateTime from,
    bool cache = false,
  }) async {
    final interval = _issInterval(timeframe);
    final candleStore = store;
    if (!cache || candleStore == null) {
      return _fetchCandles(secId, interval, from);
    }

    final key = CandleStore.keyFor(secId, '$interval');
    final known = await candleStore.load(key);
    final since = candleStore.incrementalStart(known, from);
    final fresh = await _fetchCandles(secId, interval, since ?? from);
    final merged = await candleStore.merge(key, fresh, coveredFrom: from);
    return [for (final c in merged) if (!c.time.isBefore(from)) c];
  }

  Future<List<Candle>> _fetchCandles(
    String secId,
    int interval,
    DateTime from, {
    String path = _fortsPath,
  }) async {
    Uri page(int start) => Uri.parse(
          '$_base/$path/securities/$secId/candles.json'
          '?iss.meta=off&iss.only=candles'
          '&interval=$interval&from=${_date(from)}&start=$start',
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
      // Неполная страница — последняя: холостой запрос за пустой не нужен.
      if (rows.length < candlesPageSize) break;
      start += rows.length;
    }
    return candles;
  }

  /// Коды месяцев исполнения на срочном рынке.
  static const _monthCodes = 'FGHJKMNQUVXZ';

  /// Месяцы исполнения квартальных серий: март, июнь, сентябрь, декабрь.
  static const _quarterlyMonths = [3, 6, 9, 12];

  /// Корни с ежемесячным исполнением. Остальные — квартальные.
  static const _monthlyRoots = {'BR', 'NG'};

  /// Исполняется ли контракт ежемесячно (нефть, газ) или квартально.
  ///
  /// От этого зависит и число серий за год, и ширина ликвидного окна: у
  /// месячных серий она равна месяцу, иначе соседние серии перекрывались бы
  /// и один и тот же период торговался бы в прогоне трижды.
  static bool isMonthlySeries(String secId) {
    if (secId.length < 3) return false;
    return _monthlyRoots.contains(secId.substring(0, secId.length - 2).toUpperCase());
  }

  /// Предыдущие серии того же контракта: `SiZ5` → `SiZ5, SiU5, SiM5, SiH5`.
  ///
  /// Нужны бэктесту: фьючерс ликвиден только в последнем окне своей жизни, и
  /// год истории по одному текущему контракту — это в основном мёртвая доска.
  /// Символ разбирается по факту (`база` + месяц + цифра года), а не собирается
  /// из справочника корней: у ISS база пишется как `Si`, а не `SI`.
  static List<String> previousSeries(String secId, {int count = 4}) {
    if (secId.length < 3) return [secId];
    final base = secId.substring(0, secId.length - 2);
    final monthIndex = _monthCodes.indexOf(secId[secId.length - 2].toUpperCase());
    final yearDigit = int.tryParse(secId[secId.length - 1]);
    if (monthIndex < 0 || yearDigit == null) return [secId];

    final monthly = _monthlyRoots.contains(base.toUpperCase());
    final months = monthly ? [for (var m = 1; m <= 12; m++) m] : _quarterlyMonths;
    var slot = months.indexOf(monthIndex + 1);
    if (slot < 0) return [secId];

    var year = yearDigit;
    final result = <String>[];
    while (result.length < count) {
      result.add('$base${_monthCodes[months[slot] - 1]}$year');
      slot--;
      if (slot < 0) {
        slot = months.length - 1;
        year = (year + 9) % 10;
      }
    }
    return result;
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

  /// Сколько строк просим на страницу снимка. Явное значение позволяет
  /// понять, что страница последняя, не делая лишний запрос.
  static const _snapshotPageSize = 100;

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
