import '../../domain/analysis/candle.dart';
import '../../domain/enums.dart';
import '../../domain/idea/idea.dart';
import '../../domain/models/signal.dart';
import 'bybit_client.dart';
import 'iss_client.dart';

/// Свечи для графика идеи прямо с биржи.
///
/// Решение владельца: подключённые источники данных годятся не только для
/// расчёта, но и для картинки. MOEX ISS и публичный Bybit уже настроены в
/// приложении и ключа не требуют — значит график идеи не обязан ждать
/// движка. Движок остаётся первым источником (он считал идею и знает, какие
/// именно бары под её разметкой), биржа — вторым.
///
/// Что здесь **не** делается: молчаливой подмены таймфрейма. Если на бирже
/// запрошенного таймфрейма нет, берётся ближайший доступный, и подпись
/// графика показывает тот, который реально нарисован. Иначе на экране висел
/// бы «4H» поверх часовых свечей — а по такой картинке принимают решения.
class IdeaChartSource {
  IdeaChartSource({IssClient? iss, BybitClient? bybit})
      : _iss = iss ?? IssClient(),
        _bybit = bybit ?? BybitClient();

  final IssClient _iss;
  final BybitClient _bybit;

  /// Сколько свечей просим. Больше окна разбора не нужно: график показывает
  /// сетап, а не всю историю инструмента.
  static const bars = 160;

  Future<SignalChart?> load(Idea idea) async {
    final symbol = symbolOf(idea.instrumentId);
    if (symbol.isEmpty) return null;
    final wanted = timeframeOf(idea);
    try {
      final crypto = idea.market == Market.crypto;
      final actual = crypto ? _bybitTimeframe(wanted) : _issTimeframe(wanted);
      final from = DateTime.now().subtract(
        Duration(minutes: actual.minutes * bars),
      );
      final candles = crypto
          ? await _bybit.candles(symbol, timeframe: actual, limit: bars)
          : await _iss.candles(symbol, timeframe: actual, from: from);
      if (candles.length < 2) return null;
      final window = candles.length <= bars
          ? candles
          : candles.sublist(candles.length - bars);
      return SignalChart(
        timeframeLabel: labelOf(actual),
        candles: [
          for (final c in window)
            ChartCandle(c.open, c.high, c.low, c.close, c.time),
        ],
      );
    } catch (_) {
      // График — не то, ради чего стоит рушить разбор идеи. Его отсутствие
      // видно на самом графике, и там же написана причина.
      return null;
    }
  }

  /// Тикер из канонического идентификатора: «MOEX:FUT:SIU6» → «SIU6».
  ///
  /// Движок называет инструмент площадкой, классом и кодом; биржи знают
  /// только код.
  static String symbolOf(String instrumentId) {
    final cut = instrumentId.lastIndexOf(':');
    return cut < 0 ? instrumentId : instrumentId.substring(cut + 1);
  }

  /// Таймфрейм сетапа — тот, на котором построена идея.
  ///
  /// Контекстный слишком крупен для зоны входа, триггерный слишком мелок для
  /// структуры; разметка §10.6 живёт именно на сетапном.
  static Timeframe timeframeOf(Idea idea) {
    final list = idea.timeframes;
    final raw = list.length >= 2 ? list[1] : (list.isEmpty ? '1h' : list.first);
    return parse(raw);
  }

  /// Разбор таймфрейма контракта: «1h», «4H», «1d», «15m».
  static Timeframe parse(String raw) => switch (raw.toLowerCase()) {
        '10m' => Timeframe.m10,
        '1h' || 'h1' => Timeframe.h1,
        '4h' || 'h4' => Timeframe.h4,
        '1d' || 'd1' => Timeframe.d1,
        // Пятнадцати минут в анализе нет, недельные и месячные для картинки
        // сетапа бесполезны — и то и другое сводится к дневкам.
        _ => Timeframe.d1,
      };

  static String labelOf(Timeframe timeframe) => switch (timeframe) {
        Timeframe.m10 => '10m',
        Timeframe.h1 => '1h',
        Timeframe.h4 => '4h',
        Timeframe.d1 => '1d',
      };

  /// У ISS нет четырёхчасовых свечей — клиент бросает `ArgumentError`, а не
  /// возвращает соседний интервал. Выбор делается здесь и явно: часовик.
  static Timeframe _issTimeframe(Timeframe wanted) =>
      wanted == Timeframe.h4 ? Timeframe.h1 : wanted;

  /// У Bybit нет десяти минут по той же причине — берём час.
  static Timeframe _bybitTimeframe(Timeframe wanted) =>
      wanted == Timeframe.m10 ? Timeframe.h1 : wanted;
}
