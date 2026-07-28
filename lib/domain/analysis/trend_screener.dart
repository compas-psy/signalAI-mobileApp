import '../enums.dart';
import '../models/signal.dart';
import 'donchian.dart';
import 'screener.dart';

/// Трендследящая стратегия: превращает пробой канала Дончиана в идею.
///
/// Отдельный класс, а не ветка внутри [Screener], и это сознательно. У двух
/// систем разная природа: [Screener] ищет откат к уровню на часовиках и держит
/// до пяти дней, трендследование покупает подтверждённую силу на дневках и
/// держит, пока тренд не сломается. Смешать их в одном скоринге — значит
/// получить среднее, которое не работает нигде.
///
/// Доказательная база — в документации [Donchian]. Здесь только перевод
/// сигнала в идею и честная подпись того, чем эта идея отличается.
class TrendScreener {
  const TrendScreener({
    this.entryPeriod = Donchian.fastEntry,
    this.exitPeriod = Donchian.fastExit,
    this.minRiskAtr = 0.5,
    this.maxRiskAtr = 4.0,
  });

  /// Периоды канала входа и выхода.
  final int entryPeriod;
  final int exitPeriod;

  /// Коридор допустимого риска в ATR. Слишком тесный стоп выбьет шумом,
  /// слишком широкий делает сделку неоправданно дорогой.
  final double minRiskAtr;
  final double maxRiskAtr;

  String get label => 'канал $entryPeriod/$exitPeriod дн., стоп '
      '${Donchian.stopAtr.toStringAsFixed(0)}·ATR';

  /// Оценка инструмента. null — сигнала нет.
  ///
  /// [reject] получает причину отказа — тем же способом, что и [Screener]:
  /// владелец должен видеть, почему инструмент не взят, а не только то, что
  /// его нет в списке.
  ScreenerResult? evaluate(
    InstrumentInput input, {
    void Function(String reason)? reject,
  }) {
    final spec = input.spec;
    void refuse(String reason) => reject?.call(reason);

    final signal = Donchian.evaluate(
      input.daily,
      entryPeriod: entryPeriod,
      exitPeriod: exitPeriod,
    );
    if (signal == null) {
      refuse('нет пробоя канала $entryPeriod дн.');
      return null;
    }

    final risk = signal.risk;
    if (risk <= 0) {
      refuse('нулевой риск: стоп совпал со входом');
      return null;
    }
    final riskInAtr = risk / signal.atr;
    if (riskInAtr < minRiskAtr || riskInAtr > maxRiskAtr) {
      refuse('риск ${riskInAtr.toStringAsFixed(1)}·ATR вне коридора');
      return null;
    }

    // Цена ушла от уровня входа дальше, чем стоит платить: стоп-заявка
    // исполнится сильно хуже уровня, и заявленный риск перестанет быть тем,
    // что мы посчитали.
    final drift = (input.lastPrice - signal.entry).abs();
    if (drift > risk) {
      refuse('цена ушла от уровня пробоя на '
          '${(drift / signal.atr).toStringAsFixed(1)}·ATR');
      return null;
    }

    final unitRisk = spec.riskPerUnit(risk);
    if (unitRisk <= 0) {
      refuse('нет стоимости пункта');
      return null;
    }

    final long = signal.long;
    final direction = long ? Direction.long : Direction.short;
    // Тейки здесь — вехи частичной фиксации, а не цель. Настоящий выход у
    // трендследования один: обратный пробой канала. Ставить «цель» по цене
    // значит обещать то, чего система не обещает.
    final takeProfits = [
      for (final (i, r) in [(1, 2.0), (2, 4.0), (3, 6.0)].indexed)
        TakeProfit(
          index: i + 1,
          price: long ? signal.entry + risk * r.$2 : signal.entry - risk * r.$2,
          sharePercent: const [40, 30, 30][i],
        ),
    ];

    return ScreenerResult(
      components: [
        ScoreComponent(
          name: 'Пробой канала',
          points: 1,
          maxPoints: 1,
          note: 'закрытие за границей $entryPeriod-дневного канала '
              '${_price(long ? signal.channelHigh : signal.channelLow, spec.priceDecimals)}'
              '${signal.barsSinceBreak == 0 ? '' : ', ${signal.barsSinceBreak} бар назад'}',
        ),
        ScoreComponent(
          name: 'Риск',
          points: 1,
          maxPoints: 1,
          note: 'стоп ${Donchian.stopAtr.toStringAsFixed(0)}·ATR = '
              '${riskInAtr.toStringAsFixed(1)}·ATR от входа',
        ),
        ScoreComponent(
          name: 'Выход',
          points: 1,
          maxPoints: 1,
          note: 'обратный пробой $exitPeriod-дневного канала '
              '${_price(signal.exit, spec.priceDecimals)} — не цель, а правило',
        ),
      ],
      signal: TradingSignal(
        id: '${spec.id}-trend',
        symbol: spec.symbol,
        name: spec.name,
        market: spec.market,
        direction: direction,
        // Горизонт тот же swing, но подпись честная: трендследование
        // держит недели, а не дни.
        horizon: Horizon.swing,
        horizonLabel: 'недели',
        // Скоринга у трендследования нет: сигнал двоичный — канал пробит или
        // нет. Подставлять сюда шкалу 0–100 значило бы изобразить точность,
        // которой в системе не заложено.
        score: 70,
        entry: signal.entry,
        stopLoss: signal.stop,
        takeProfits: takeProfits,
        priceDecimals: spec.priceDecimals,
        riskReward: '2,0',
        chips: [
          'ТРЕНД',
          'канал $entryPeriod/$exitPeriod',
          if (signal.barsSinceBreak > 0) 'пробой ${signal.barsSinceBreak} бар назад',
        ],
        note: 'Пробой $entryPeriod-дневного канала. Вход стоп-заявкой: '
            'покупаем подтверждённую силу, а не откат. Выход — обратный пробой '
            '$exitPeriod-дневного канала, а не цена: у трендследования нет цели, '
            'есть правило выхода. Тейки — вехи частичной фиксации.',
        factors: [
          SignalFactor(
            name: 'Доказательная база',
            text: 'Временной моментум документирован на 58 фьючерсах '
                '(JFE 2012) и на 67 рынках с 1880 года (JPM 2017). На нашей '
                'годовой истории FORTS это не проверяется — дневных пробоев '
                'слишком мало.',
            weight: 1,
          ),
        ],
        events: const [],
        unitRisk: unitRisk,
        unitRiskLabel: '${unitRisk.round()} ₽ / ${spec.unitRiskSuffix}',
        unitMultiplier: spec.unitMultiplier,
        unitDecimals: spec.unitDecimals,
        unitName: spec.unitName,
        lastPrice:
            input.lastPrice.toStringAsFixed(spec.priceDecimals).replaceAll('.', ','),
        changeLabel: input.changePercentLabel,
        changeUp: input.changeUp,
        status: SignalStatus.proposed,
        // Вход только стоп-заявкой — это часть стратегии, а не настройка.
        entryIsStop: true,
        invalidationPrice: signal.stop,
        correlationGroup: correlationGroupFor(spec.symbol, spec.market),
      ),
    );
  }

  static String _price(double value, int decimals) =>
      value.toStringAsFixed(decimals).replaceAll('.', ',');
}
