import '../../domain/analysis/candle.dart';
import '../local_store.dart';

/// Постоянное хранилище свечей: история докачивается, а не качается заново.
///
/// Мотив прямой: год часовиков по 44 сериям FORTS — это сотни запросов и
/// десятки мегабайт трафика. История прошедших часов не меняется никогда, а у
/// истёкшего контракта не меняется вообще ничего, поэтому качать её повторно
/// незачем: с биржи забирается только хвост от последней известной свечи.
///
/// Формат хранения плоский — шесть чисел на свечу вместо объекта с ключами.
/// На пяти тысячах свечей это разница между ~300 КБ и ~1,5 МБ на серию, и
/// разбор такого файла занимает миллисекунды, а не сотни.
class CandleStore {
  CandleStore(this._store);

  final LocalStore _store;

  /// Кэш в памяти поверх диска: за один пересчёт одна серия читается не раз.
  final Map<String, StoredCandles> _memory = {};

  /// Сколько свечей держим по одной серии.
  ///
  /// Год часовиков FORTS — около 3 500 свечей, крипты — 8 760. Запас сверху
  /// нужен, чтобы редкие длинные запросы не подрезали историю, но телефон
  /// архивом бирж быть не должен.
  static const maxCandles = 12000;

  static String keyFor(String symbol, String interval) => '$symbol|$interval';

  String _fileName(String key) =>
      'candles_${key.replaceAll(RegExp(r'[^A-Za-z0-9]'), '_')}';

  /// Сохранённая история серии, по возрастанию времени.
  Future<StoredCandles> load(String key) async {
    final cached = _memory[key];
    if (cached != null) return cached;

    final json = await _store.read(_fileName(key));
    final flat = json?['v'];
    if (flat is! List) return _memory[key] = StoredCandles.empty;

    final candles = <Candle>[];
    for (var i = 0; i + 5 < flat.length; i += 6) {
      final time = (flat[i] as num?)?.toInt();
      if (time == null) continue;
      candles.add(Candle(
        time: DateTime.fromMillisecondsSinceEpoch(time, isUtc: true),
        open: (flat[i + 1] as num?)?.toDouble() ?? 0,
        high: (flat[i + 2] as num?)?.toDouble() ?? 0,
        low: (flat[i + 3] as num?)?.toDouble() ?? 0,
        close: (flat[i + 4] as num?)?.toDouble() ?? 0,
        volume: (flat[i + 5] as num?)?.toDouble() ?? 0,
      ));
    }
    return _memory[key] = StoredCandles(
      candles: candles,
      coveredFrom: DateTime.tryParse(json?['from'] as String? ?? ''),
    );
  }

  /// Досохранение: [fresh] вливается в известную историю по времени свечи.
  ///
  /// Совпадающие метки перезаписываются свежими — последняя известная свеча
  /// почти всегда незакрытая, и её значения ещё меняются.
  Future<List<Candle>> merge(
    String key,
    List<Candle> fresh, {
    required DateTime coveredFrom,
  }) async {
    final known = await load(key);
    final previous = known.coveredFrom;
    // Глубина покрытия только растёт: если однажды скачали с более ранней
    // даты, повторно её просить не нужно.
    final covered = previous == null || coveredFrom.isBefore(previous)
        ? coveredFrom
        : previous;

    final byTime = <int, Candle>{
      for (final c in known.candles) c.time.millisecondsSinceEpoch: c,
      for (final c in fresh) c.time.millisecondsSinceEpoch: c,
    };
    final times = byTime.keys.toList()..sort();
    var merged = [for (final t in times) byTime[t]!];
    if (merged.length > maxCandles) {
      merged = merged.sublist(merged.length - maxCandles);
    }

    _memory[key] = StoredCandles(candles: merged, coveredFrom: covered);
    await _store.write(_fileName(key), {
      'from': covered.toIso8601String(),
      'v': [
        for (final c in merged)
          ...[
            c.time.millisecondsSinceEpoch,
            c.open,
            c.high,
            c.low,
            c.close,
            c.volume,
          ],
      ],
    });
    return merged;
  }

  /// С какого момента догружать историю. null — качаем с [from] целиком.
  ///
  /// Решает не первая сохранённая свеча, а глубина, с которой мы уже качали:
  /// у контракта может просто не быть свечей раньше какой-то даты, и путать
  /// «биржа столько не отдаёт» с «мы столько не просили» нельзя — иначе
  /// история каждый раз выглядела бы неполной и качалась заново.
  DateTime? incrementalStart(StoredCandles known, DateTime from) {
    if (known.candles.isEmpty) return null;
    final covered = known.coveredFrom;
    if (covered == null || covered.isAfter(from)) return null;
    // Последняя свеча перезапрашивается: на момент прошлой загрузки её час мог
    // быть ещё не закрыт.
    return known.candles.last.time;
  }
}

/// Сохранённая история серии и глубина, с которой она качалась.
class StoredCandles {
  const StoredCandles({required this.candles, required this.coveredFrom});

  final List<Candle> candles;

  /// Самая ранняя дата, за которую мы уже ходили на биржу.
  final DateTime? coveredFrom;

  static const empty = StoredCandles(candles: [], coveredFrom: null);
}
