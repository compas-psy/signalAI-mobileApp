import 'candle.dart';
import 'indicators.dart';

/// Пробой канала Дончиана — сигнал трендследящей системы.
///
/// Зачем это здесь, если уже есть скринер. Нынешняя стратегия построена на
/// структуре 1H (BOS/CHoCH), Price Action и RSI — то есть на трейдерской
/// традиции, у которой я не нашёл ни одного публично документированного
/// подтверждения на фьючерсах после издержек. Трендследование — единственный
/// подход в нашем классе активов с многодесятилетней внешней доказательной
/// базой: временной моментум показан на 58 ликвидных фьючерсах
/// (Moskowitz, Ooi, Pedersen, JFE 2012), на 67 рынках с 1880 года
/// (Hurst, Ooi, Pedersen, JPM 2017) и с t-статистикой около 10 с 1800 года
/// (Lempérière et al., 2014).
///
/// Честная оговорка, которую нельзя терять: подтвердить это на нашей годовой
/// истории FORTS невозможно — дневных пробоев за год слишком мало. Мы
/// опираемся на внешние публикации, а собственный прогон используем как
/// проверку реализации, а не как доказательство доходности.
///
/// Правила — по первоисточнику «Original Turtle Trading Rules»: вход по
/// пробою канала за [entryPeriod] дней, стоп на [stopAtr] · ATR(20), выход по
/// обратному пробою за [exitPeriod] дней.
class DonchianSignal {
  const DonchianSignal({
    required this.long,
    required this.entry,
    required this.stop,
    required this.exit,
    required this.atr,
    required this.channelHigh,
    required this.channelLow,
    required this.barsSinceBreak,
  });

  /// Направление пробоя.
  final bool long;

  /// Уровень входа: граница канала. Вход стоп-заявкой — покупаем
  /// подтверждённую силу, а не откат. Лимитка на пробитом уровне даёт
  /// отрицательный отбор: она наполняется каждым ложным пробоем и пропускает
  /// движения, которые уходят без возврата.
  final double entry;

  /// Защитный стоп.
  final double stop;

  /// Уровень выхода по обратному пробою — не цель, а правило сопровождения.
  final double exit;

  final double atr;
  final double channelHigh;
  final double channelLow;

  /// Сколько баров назад произошёл пробой. 0 — на последнем баре.
  final int barsSinceBreak;

  /// Риск на единицу — расстояние до стопа.
  double get risk => (entry - stop).abs();
}

/// Расчёт трендследящего сигнала по дневным свечам.
abstract final class Donchian {
  /// Классические периоды системы: быстрая (20/10) и медленная (55/20).
  ///
  /// Оба варианта из первоисточника. Медленная берёт меньше сделок и держит
  /// дольше; быстрая чувствительнее к развороту.
  static const fastEntry = 20;
  static const fastExit = 10;
  static const slowEntry = 55;
  static const slowExit = 20;

  /// Множитель стопа: 2·N, где N = ATR(20). Из правил Turtle.
  static const stopAtr = 2.0;

  /// Сигнал на последнем баре либо null.
  ///
  /// [freshBars] — сколько баров назад пробой ещё считается свежим. Пробой
  /// недельной давности торговать поздно: цена уже ушла, а стоп остался на
  /// прежнем расстоянии, и соотношение риска к движению испорчено.
  static DonchianSignal? evaluate(
    List<Candle> daily, {
    int entryPeriod = fastEntry,
    int exitPeriod = fastExit,
    int atrPeriod = 20,
    int freshBars = 2,
  }) {
    // Канал считается по барам ДО текущего: сравнивать бар сам с собой значит
    // объявлять пробоем любой новый максимум, включая тот, что образован
    // собственным хвостом.
    final need = entryPeriod + atrPeriod + 2;
    if (daily.length < need) return null;

    final atrValues = atr(daily, period: atrPeriod);
    final last = daily.length - 1;

    for (var back = 0; back <= freshBars; back++) {
      final i = last - back;
      if (i - entryPeriod < 0) continue;
      final window = daily.sublist(i - entryPeriod, i);
      if (window.isEmpty) continue;

      final high = window.map((c) => c.high).reduce((a, b) => a > b ? a : b);
      final low = window.map((c) => c.low).reduce((a, b) => a < b ? a : b);
      final bar = daily[i];
      final n = atrValues[i];
      if (n == null || n <= 0) continue;

      final brokeUp = bar.close > high;
      final brokeDown = bar.close < low;
      if (!brokeUp && !brokeDown) continue;

      // Выходной канал — на момент сигнала, из тех же баров.
      final exitWindow = daily.sublist(
        (i - exitPeriod).clamp(0, daily.length),
        i,
      );
      if (exitWindow.isEmpty) continue;
      final exit = brokeUp
          ? exitWindow.map((c) => c.low).reduce((a, b) => a < b ? a : b)
          : exitWindow.map((c) => c.high).reduce((a, b) => a > b ? a : b);

      final entry = brokeUp ? high : low;
      return DonchianSignal(
        long: brokeUp,
        entry: entry,
        stop: brokeUp ? entry - stopAtr * n : entry + stopAtr * n,
        exit: exit,
        atr: n,
        channelHigh: high,
        channelLow: low,
        barsSinceBreak: back,
      );
    }
    return null;
  }
}
