import '../../domain/enums.dart';
import '../../domain/models/digest.dart';
import '../../domain/models/portfolio.dart';
import '../../domain/models/settings.dart';
import '../../domain/models/signal.dart';
import '../../domain/models/strategy.dart';
import '../repository.dart';

/// Демонстрационные данные — ровно те, что в макете Claude Design
/// (`design/SignalAI App.dc.html`).
///
/// Используются, пока не задан адрес мобильного гейтвея
/// (`--dart-define=SIGNALAI_API_BASE_URL=…`): приложение полностью
/// работоспособно офлайн, интерфейс можно смотреть и кликать без сервера.
/// Ни одна цифра отсюда не является рыночной — это фикстура.
class DemoRepository implements SignalAiRepository {
  DemoRepository();

  final Set<String> _confirmed = <String>{};
  Map<String, bool> _strategyEnabled = {'s1': true, 's2': true, 's3': false};
  Map<String, bool> _channels = {'push': true, 'telegram': true, 'max': true};
  Map<String, bool> _notifications = {'digest': true, 'alerts': true, 'events': true};
  bool _binanceConnected = false;
  bool _backtestFresh = false;
  RiskProfile _risk = const RiskProfile(
    deposit: 2400000,
    riskPercent: 0.75,
    dailyLossLimit: '−2% · автостоп',
    maxConcurrentTrades: 'до 3',
    pauseRule: 'пауза до завтра',
  );

  /// Небольшая задержка, чтобы демо-режим вёл себя как сеть.
  static Future<T> _delayed<T>(T value) =>
      Future<T>.delayed(const Duration(milliseconds: 120), () => value);

  @override
  Future<DailyDigest> fetchDigest() => _delayed(
        DailyDigest(
          title: 'Утренний дайджест',
          subtitle: 'Сб, 25 июля · 10:10 МСК',
          deliveryBadges: const ['TG ✓', 'MAX ✓'],
          regime: const [
            RegimeQuote(name: 'IMOEX', value: '−0,42%', tone: Tone.negative),
            RegimeQuote(name: 'Si', value: '+0,31%', tone: Tone.positive),
            RegimeQuote(name: 'BR', value: '−1,12%', tone: Tone.negative),
            RegimeQuote(name: 'BTC', value: '+1,84%', tone: Tone.positive),
            RegimeQuote(name: 'RVI', value: '28,4', tone: Tone.neutral),
          ],
          regimeNote: 'Режим смешанный: рубль слабеет, нефть под давлением. '
              'Приоритет — лонги Si и BTC, шорты BR и индекса. '
              'Не берём лонги акций против падающего MX.',
          events: const [
            MarketEvent(
              time: '16:30',
              text: 'Решение ЦБ по ключевой ставке',
              impact: EventImpact.high,
              affects: 'MXU6 · Si',
            ),
            MarketEvent(
              time: '15:30',
              text: 'Розничные продажи США',
              impact: EventImpact.mid,
              affects: 'BTCUSDT',
            ),
            MarketEvent(
              time: '28.07',
              text: 'Набсовет Сбербанка — дивиденды',
              impact: EventImpact.high,
              affects: 'SBER',
            ),
          ],
          signalsQuota: '5 из 5',
          signals: _signals(),
        ),
      );

  List<TradingSignal> _signals() => [
        _signal(
          id: 'si',
          symbol: 'SiU6',
          name: 'Доллар/Рубль · сент 2026',
          market: Market.forts,
          direction: Direction.long,
          horizonLabel: '1–3 дня',
          score: 87,
          entry: 87450,
          stopLoss: 86900,
          takeProfitPrices: const [88200, 88700, 89400],
          priceDecimals: 0,
          riskReward: '2,3',
          unitRisk: 550,
          unitRiskLabel: '550 ₽ / контракт',
          unitName: 'конт.',
          lastPrice: '87 480',
          changeLabel: '+0,31%',
          changeUp: true,
          correlationGroup: 'currency',
          chips: const ['Spring', 'OB 1H', 'RSI-див', 'OI +4,2%'],
          note: 'Спринг из фазы C накопления по Вайкоффу, слом структуры (BOS) на 1H '
              'и ретест ордер-блока. Вход на пин-баре от OB, инвалидация — '
              'закрепление ниже 87 300.',
          invalidationPrice: 87300,
          factors: const [
            SignalFactor(
              name: 'Wyckoff',
              text: 'Фаза C накопления, spring 87 050 с тестом на падающем объёме',
              weight: 3,
            ),
            SignalFactor(
              name: 'Smart Money',
              text: 'BOS 1H на 87 750, ретест OB 87 300–87 500, FVG закрыт',
              weight: 3,
            ),
            SignalFactor(
              name: 'Price Action',
              text: 'Пин-бар 1H от верхней границы OB, ложный пробой 87 300',
              weight: 2,
            ),
            SignalFactor(
              name: 'RSI(14)',
              text: '1H: бычья дивергенция на спринге · 1D: 44, нейтрально',
              weight: 2,
            ),
            SignalFactor(
              name: 'Эллиотт',
              text: 'Старшая волна 3 вверх, локальная коррекция (ii) завершена',
              weight: 1,
            ),
            SignalFactor(
              name: 'Объём / OI',
              text: 'OI +4,2% д/д на росте — набор лонга; кульминация продаж на спринге',
              weight: 3,
            ),
          ],
          events: const [
            MarketEvent(
              time: '14:00',
              text: 'Дневной клиринг FORTS — пересчёт ГО',
              impact: EventImpact.low,
            ),
            MarketEvent(
              time: '16:30',
              text: 'Аукцион Минфина по ОФЗ — волатильность рубля',
              impact: EventImpact.mid,
            ),
          ],
        ),
        _signal(
          id: 'br',
          symbol: 'BR-9.26',
          name: 'Нефть Brent · сент 2026',
          market: Market.forts,
          direction: Direction.short,
          horizonLabel: '1–2 дня',
          score: 79,
          entry: 82.14,
          stopLoss: 83.05,
          takeProfitPrices: const [80.90, 80.10, 79.20],
          priceDecimals: 2,
          riskReward: '2,2',
          unitRisk: 710,
          unitRiskLabel: '710 ₽ / контракт',
          unitName: 'конт.',
          lastPrice: '82,08',
          changeLabel: '−1,12%',
          changeUp: false,
          correlationGroup: 'energy',
          chips: const ['UTAD', 'CHoCH 1H', 'FVG', 'OI↑ на падении'],
          note: 'Распределение по Вайкоффу с UTAD на 83.60, CHoCH на 1H и незакрытый FVG снизу. '
              'Шорт от ретеста зоны 82.10–82.40.',
          invalidationPrice: 83.05,
          factors: const [
            SignalFactor(
              name: 'Wyckoff',
              text: 'Распределение, UTAD 83,60 без продолжения',
              weight: 3,
            ),
            SignalFactor(
              name: 'Smart Money',
              text: 'CHoCH 1H, ордер-блок 82,10–82,40, FVG до 80,90',
              weight: 3,
            ),
            SignalFactor(
              name: 'Price Action',
              text: 'Медвежье поглощение D1 от сопротивления',
              weight: 2,
            ),
            SignalFactor(
              name: 'RSI(14)',
              text: '1D: 68 → разворот вниз · 1H: медвежья дивергенция',
              weight: 2,
            ),
            SignalFactor(
              name: 'Эллиотт',
              text: 'Волна C нисходящей коррекции',
              weight: 1,
            ),
            SignalFactor(
              name: 'Объём / OI',
              text: 'OI растёт на падении — набор шорта',
              weight: 2,
            ),
          ],
          events: const [
            MarketEvent(
              time: 'чт',
              text: 'Запасы нефти EIA — перенос отчёта',
              impact: EventImpact.mid,
            ),
            MarketEvent(
              time: '19:05',
              text: 'Вечерний клиринг FORTS',
              impact: EventImpact.low,
            ),
          ],
        ),
        _signal(
          id: 'btc',
          symbol: 'BTCUSDT',
          name: 'Bybit · перпетуал',
          market: Market.crypto,
          direction: Direction.long,
          horizonLabel: '2–5 дней',
          score: 82,
          entry: 118250,
          stopLoss: 115800,
          takeProfitPrices: const [121400, 124800, 129000],
          priceDecimals: 0,
          riskReward: '2,7',
          unitRisk: 1910,
          unitRiskLabel: '1 910 ₽ / 0,01 BTC',
          unitMultiplier: 0.01,
          unitDecimals: 2,
          unitName: 'BTC',
          lastPrice: '118 640',
          changeLabel: '+1,84%',
          changeUp: true,
          correlationGroup: 'crypto',
          chips: const ['OB 4H', 'Свип лоу', 'Funding −', 'Vol↑'],
          note: 'Свип недельного лоу со сбором ликвидности, реакция от ордер-блока 4H. '
              'Отрицательный фандинг при росте — топливо для шорт-сквиза.',
          invalidationPrice: 115800,
          factors: const [
            SignalFactor(
              name: 'Wyckoff',
              text: 'Ре-аккумуляция над 114 000, спринг-свип лоу',
              weight: 2,
            ),
            SignalFactor(
              name: 'Smart Money',
              text: 'OB 4H 115 800–117 200, свип equal lows, BOS на 1H',
              weight: 3,
            ),
            SignalFactor(
              name: 'Price Action',
              text: 'Бычье поглощение 4H после свипа',
              weight: 2,
            ),
            SignalFactor(
              name: 'RSI(14)',
              text: '4H: 41 → 55, бычья дивергенция на свипе',
              weight: 2,
            ),
            SignalFactor(
              name: 'Эллиотт',
              text: 'Начало волны 3 после плоской коррекции',
              weight: 1,
            ),
            SignalFactor(
              name: 'Объём / OI',
              text: 'Объём растёт на росте, funding −0,008% — шорты платят',
              weight: 3,
            ),
          ],
          events: const [
            MarketEvent(
              time: '15:30',
              text: 'Розничные продажи США — волатильность USD',
              impact: EventImpact.mid,
            ),
            MarketEvent(
              time: 'пн',
              text: 'Экспирация опционов Deribit \$4,1 млрд',
              impact: EventImpact.mid,
            ),
          ],
        ),
        _signal(
          id: 'sber',
          symbol: 'SBER',
          name: 'Сбербанк · акции MOEX',
          market: Market.moex,
          direction: Direction.long,
          horizonLabel: '3–5 дней',
          score: 74,
          entry: 342.6,
          stopLoss: 336.8,
          takeProfitPrices: const [351.0, 358.0, 366.0],
          priceDecimals: 1,
          riskReward: '2,6',
          unitRisk: 58,
          unitRiskLabel: '58 ₽ / лот (10 акций)',
          unitName: 'лот.',
          lastPrice: '342,15',
          changeLabel: '+0,42%',
          changeUp: true,
          correlationGroup: 'index',
          chips: const ['Ретест BOS', 'Пин-бар D1', 'Объём'],
          note: 'Ретест сломанного уровня 341 после BOS на D1. Пин-бар на дневке с ростом объёма. '
              'Идея против слабого индекса — сниженный размер позиции.',
          invalidationPrice: 336.8,
          factors: const [
            SignalFactor(
              name: 'Wyckoff',
              text: 'Выход из ре-аккумуляции 328–341',
              weight: 2,
            ),
            SignalFactor(
              name: 'Smart Money',
              text: 'BOS D1, ретест зоны спроса 340–342',
              weight: 2,
            ),
            SignalFactor(
              name: 'Price Action',
              text: 'Пин-бар D1 от уровня, ложный пробой 340',
              weight: 3,
            ),
            SignalFactor(
              name: 'RSI(14)',
              text: '1D: 58 — тренд без перекупленности',
              weight: 2,
            ),
            SignalFactor(
              name: 'Эллиотт',
              text: 'Волна 3 среднесрочного импульса',
              weight: 1,
            ),
            SignalFactor(
              name: 'Объём / OI',
              text: 'Объём выше среднего на отскоке',
              weight: 2,
            ),
          ],
          events: const [
            MarketEvent(
              time: '28.07',
              text: 'Набсовет: дивиденды за 1П2026',
              impact: EventImpact.high,
            ),
            MarketEvent(
              time: '16:30',
              text: 'Решение ЦБ по ключевой ставке',
              impact: EventImpact.high,
            ),
          ],
        ),
        _signal(
          id: 'mx',
          symbol: 'MXU6',
          name: 'Индекс МосБиржи · сент 2026',
          market: Market.forts,
          direction: Direction.short,
          horizonLabel: '1–2 дня',
          score: 71,
          entry: 4215,
          stopLoss: 4262,
          takeProfitPrices: const [4140, 4095, 4030],
          priceDecimals: 0,
          riskReward: '2,6',
          unitRisk: 470,
          unitRiskLabel: '470 ₽ / контракт',
          unitName: 'конт.',
          lastPrice: '4 209',
          changeLabel: '−0,42%',
          changeUp: false,
          correlationGroup: 'index',
          chips: const ['Распределение', 'RSI 1D 72', 'CHoCH'],
          note: 'Распределение у годового максимума, RSI 1D в перекупленности, CHoCH на 1H. '
              'Шорт от ретеста 4 215 с целями к нижней границе диапазона.',
          invalidationPrice: 4262,
          factors: const [
            SignalFactor(
              name: 'Wyckoff',
              text: 'Распределение 4 190–4 260, признаки SOW',
              weight: 3,
            ),
            SignalFactor(
              name: 'Smart Money',
              text: 'CHoCH 1H, ордер-блок 4 215–4 240',
              weight: 2,
            ),
            SignalFactor(
              name: 'Price Action',
              text: 'Серия ложных пробоев 4 250',
              weight: 2,
            ),
            SignalFactor(
              name: 'RSI(14)',
              text: '1D: 72 — перекупленность, медвежья дивергенция',
              weight: 3,
            ),
            SignalFactor(
              name: 'Эллиотт',
              text: 'Завершение волны 5 старшего импульса',
              weight: 1,
            ),
            SignalFactor(
              name: 'Объём / OI',
              text: 'OI падает на росте — лонги фиксируются',
              weight: 2,
            ),
          ],
          events: const [
            MarketEvent(
              time: '16:30',
              text: 'Решение ЦБ по ключевой ставке',
              impact: EventImpact.high,
            ),
            MarketEvent(
              time: '14:00',
              text: 'Дневной клиринг FORTS',
              impact: EventImpact.low,
            ),
          ],
        ),
      ];

  TradingSignal _signal({
    required String id,
    required String symbol,
    required String name,
    required Market market,
    required Direction direction,
    required String horizonLabel,
    required int score,
    required double entry,
    required double stopLoss,
    required List<double> takeProfitPrices,
    required int priceDecimals,
    required String riskReward,
    required double unitRisk,
    required String unitRiskLabel,
    required String unitName,
    required String lastPrice,
    required String changeLabel,
    required bool changeUp,
    required List<String> chips,
    required String note,
    required List<SignalFactor> factors,
    required List<MarketEvent> events,
    double unitMultiplier = 1,
    int unitDecimals = 0,
    String? correlationGroup,
    double? invalidationPrice,
  }) {
    // Доли фиксации по тейкам — из макета: 50 / 30 / 20 %.
    const shares = [50, 30, 20];
    return TradingSignal(
      id: id,
      symbol: symbol,
      name: name,
      market: market,
      direction: direction,
      horizon: Horizon.swing,
      horizonLabel: horizonLabel,
      score: score,
      entry: entry,
      stopLoss: stopLoss,
      takeProfits: [
        for (var i = 0; i < takeProfitPrices.length; i++)
          TakeProfit(
            index: i + 1,
            price: takeProfitPrices[i],
            sharePercent: i < shares.length ? shares[i] : 0,
          ),
      ],
      priceDecimals: priceDecimals,
      riskReward: riskReward,
      chips: chips,
      note: note,
      factors: factors,
      events: events,
      unitRisk: unitRisk,
      unitRiskLabel: unitRiskLabel,
      unitMultiplier: unitMultiplier,
      unitDecimals: unitDecimals,
      unitName: unitName,
      lastPrice: lastPrice,
      changeLabel: changeLabel,
      changeUp: changeUp,
      status: _confirmed.contains(id) ? SignalStatus.working : SignalStatus.pushed,
      correlationGroup: correlationGroup,
      invalidationPrice: invalidationPrice,
      strategyId: market == Market.crypto ? 's2' : 's1',
    );
  }

  @override
  Future<TradesSummary> fetchTrades() => _delayed(
        const TradesSummary(
          equityTitle: 'Эквити · 30 дней',
          equityChange: '+8,4%',
          equityCurve: [
            0, .4, .2, .9, .7, 1.5, 1.2, 2, 1.7, 2.6, 2.3, 3.2, 2.9,
            3.8, 3.5, 4.3, 5, 4.6, 5.6, 6.2, 5.9, 7, 7.7, 7.3, 8.4,
          ],
          stats: [
            StatTile(value: '61%', label: 'винрейт'),
            StatTile(value: '+0,62R', label: 'ср. сделка', tone: Tone.positive),
            StatTile(value: '2,1', label: 'профит-фактор'),
            StatTile(value: '38', label: 'сделок'),
          ],
          positions: [
            ActivePosition(
              symbol: 'SiU6',
              direction: Direction.long,
              entryLabel: '87 450',
              currentLabel: '87 890',
              pnlLabel: '+14 080 ₽',
              pnlPositive: true,
              progressPercent: 59,
              stage: 'До TP1 осталось 310 п. · SL на месте',
            ),
            ActivePosition(
              symbol: 'BTCUSDT',
              direction: Direction.long,
              entryLabel: '118 250',
              currentLabel: '119 905',
              pnlLabel: '+9 340 ₽',
              pnlPositive: true,
              progressPercent: 52,
              stage: 'TP1 ✓ 50% зафиксировано · SL в безубыток',
            ),
          ],
          journal: [
            JournalEntry(
              date: '24.07',
              symbol: 'SiU6',
              direction: Direction.long,
              outcome: 'TP2',
              rMultiple: 2.1,
            ),
            JournalEntry(
              date: '23.07',
              symbol: 'BR-8.26',
              direction: Direction.short,
              outcome: 'SL',
              rMultiple: -1.0,
            ),
            JournalEntry(
              date: '23.07',
              symbol: 'BTCUSDT',
              direction: Direction.long,
              outcome: 'TP1',
              rMultiple: 1.4,
            ),
            JournalEntry(
              date: '22.07',
              symbol: 'SBER',
              direction: Direction.long,
              outcome: 'TP3',
              rMultiple: 2.8,
            ),
            JournalEntry(
              date: '21.07',
              symbol: 'MXU6',
              direction: Direction.short,
              outcome: 'эксп.',
              rMultiple: 0.9,
            ),
            JournalEntry(
              date: '20.07',
              symbol: 'NG-7.26',
              direction: Direction.short,
              outcome: 'SL',
              rMultiple: -1.0,
            ),
            JournalEntry(
              date: '20.07',
              symbol: 'ETHUSDT',
              direction: Direction.long,
              outcome: 'TP2',
              rMultiple: 2.2,
            ),
          ],
        ),
      );

  static const _staleBacktest = BacktestResult(
    info: 'Последний прогон · 180 дней · 214 сделок',
    stats: [
      StatTile(value: '+41%', label: 'доходность', tone: Tone.positive),
      StatTile(value: '58%', label: 'винрейт'),
      StatTile(value: '1,9', label: 'профит-фактор'),
      StatTile(value: '−6,2%', label: 'макс. просадка', tone: Tone.negative),
    ],
    equityCurve: [
      0, .5, .2, 1, .8, 1.6, 1.3, 2.2, 1.9, 2.9,
      2.5, 3.7, 3.3, 4.4, 4, 5.1, 5.8, 5.4, 6.4, 7,
    ],
  );

  static const _freshBacktest = BacktestResult(
    info: '180 дней · 221 сделка · только что',
    stats: [
      StatTile(value: '+47%', label: 'доходность', tone: Tone.positive),
      StatTile(value: '59%', label: 'винрейт'),
      StatTile(value: '2,0', label: 'профит-фактор'),
      StatTile(value: '−5,8%', label: 'макс. просадка', tone: Tone.negative),
    ],
    equityCurve: [
      0, .6, .3, 1.2, 1, 1.9, 1.6, 2.6, 2.2, 3.4,
      3, 4.4, 4, 5.2, 4.7, 6.1, 6.9, 6.4, 7.6, 8.3,
    ],
  );

  @override
  Future<StrategiesSnapshot> fetchStrategies() => _delayed(
        StrategiesSnapshot(
          packs: [
            StrategyPack(
              id: 's1',
              name: 'Интеграционная · MOEX FORTS',
              description: 'Wyckoff + SMC + Price Action + RSI + Эллиотт + объём/OI. '
                  'TOP-10 по обороту.',
              statsLabel: 'WR 61% · PF 2,1 · 38 сделок / 90д',
              enabled: _strategyEnabled['s1'] ?? true,
              mode: StrategyMode.live,
              closedPaperTrades: 38,
            ),
            StrategyPack(
              id: 's2',
              name: 'Crypto SMC · Bybit',
              description: 'Ордер-блоки, свипы ликвидности, funding-фильтр. BTC, ETH, SOL.',
              statsLabel: 'WR 57% · PF 1,8 · 52 сделки / 90д',
              enabled: _strategyEnabled['s2'] ?? true,
              mode: StrategyMode.live,
              closedPaperTrades: 52,
            ),
            StrategyPack(
              id: 's3',
              name: 'Свинг · Акции МосБиржи',
              description: 'Ретесты BOS на D1, фильтр дивидендных отсечек.',
              statsLabel: 'WR 54% · PF 1,6 · 21 сделка / 90д',
              enabled: _strategyEnabled['s3'] ?? false,
              closedPaperTrades: 21,
            ),
          ],
          paramsTitle: 'Параметры · Интеграционная',
          params: [
            StrategyParam(
              name: 'Риск на сделку',
              value: '${_riskPercentLabel()} депозита',
            ),
            const StrategyParam(name: 'Мин. R:R до TP2', value: '2,0'),
            const StrategyParam(name: 'Идей в день', value: 'до 5'),
            const StrategyParam(name: 'Фильтр экспирации', value: 'больше 3 торговых дней'),
            const StrategyParam(name: 'Таймфреймы', value: '1H · 1D (10м — уровни)'),
            const StrategyParam(name: 'Вселенная', value: 'TOP-10 по обороту + BTC/ETH'),
            const StrategyParam(name: 'Режим-фильтр', value: 'не против индекса и валюты'),
          ],
          backtest: _backtestFresh ? _freshBacktest : _staleBacktest,
        ),
      );

  String _riskPercentLabel() {
    final p = _risk.riskPercent;
    final s = p == p.roundToDouble() ? p.toStringAsFixed(0) : p.toString();
    return '${s.replaceAll('.', ',')}%';
  }

  @override
  Future<SettingsSnapshot> fetchSettings() => _delayed(
        SettingsSnapshot(
          exchanges: [
            const ExchangeAccount(
              id: 'tinvest',
              abbr: 'T',
              name: 'Т-Инвестиции API',
              subtitle: 'MOEX: фьючерсы и акции · токен активен',
              connected: true,
              accentHex: 0xFFFFD400,
            ),
            const ExchangeAccount(
              id: 'bybit',
              abbr: 'B',
              name: 'Bybit',
              subtitle: 'Перпетуалы · ключ read+trade',
              connected: true,
              accentHex: 0xFFF7A600,
            ),
            ExchangeAccount(
              id: 'binance',
              abbr: 'BN',
              name: 'Binance',
              subtitle: _binanceConnected ? 'Спот · ключ read+trade' : 'Не подключено',
              connected: _binanceConnected,
              accentHex: 0xFF8E8E98,
            ),
          ],
          channels: [
            ToggleSetting(
              id: 'telegram',
              name: 'Telegram-бот',
              subtitle: '@signalai_bot · идеи + алерты TP/SL',
              enabled: _channels['telegram'] ?? true,
            ),
            ToggleSetting(
              id: 'max',
              name: 'MAX',
              subtitle: 'Личный чат · дублирование дайджеста',
              enabled: _channels['max'] ?? true,
            ),
            ToggleSetting(
              id: 'push',
              name: 'Пуш-уведомления',
              subtitle: 'Android · критичные события мгновенно',
              enabled: _channels['push'] ?? true,
            ),
          ],
          notifications: [
            ToggleSetting(
              id: 'digest',
              name: 'Утренний дайджест',
              subtitle: 'Будни · 10:10 МСК · топ-5 идей',
              enabled: _notifications['digest'] ?? true,
            ),
            ToggleSetting(
              id: 'alerts',
              name: 'Срабатывания TP / SL',
              subtitle: 'По каждой активной сделке',
              enabled: _notifications['alerts'] ?? true,
            ),
            ToggleSetting(
              id: 'events',
              name: 'События по активным идеям',
              subtitle: 'Новости и календарь — только по вашим позициям',
              enabled: _notifications['events'] ?? true,
            ),
          ],
          risk: _risk,
        ),
      );

  @override
  Future<void> confirmSignal(String signalId) async {
    _confirmed.add(signalId);
  }

  @override
  Future<void> setStrategyEnabled(String strategyId, bool enabled) async {
    _strategyEnabled = {..._strategyEnabled, strategyId: enabled};
  }

  @override
  Future<BacktestResult> runBacktest(String strategyId) async {
    await Future<void>.delayed(const Duration(milliseconds: 1700));
    _backtestFresh = true;
    return _freshBacktest;
  }

  @override
  Future<ExchangeAccount> connectExchange(String exchangeId) async {
    if (exchangeId == 'binance') _binanceConnected = true;
    final settings = await fetchSettings();
    return settings.exchanges.firstWhere((e) => e.id == exchangeId);
  }

  @override
  Future<void> setChannelEnabled(String channelId, bool enabled) async {
    _channels = {..._channels, channelId: enabled};
  }

  @override
  Future<void> setNotificationEnabled(String notificationId, bool enabled) async {
    _notifications = {..._notifications, notificationId: enabled};
  }

  @override
  Future<RiskProfile> updateRiskProfile({double? deposit, double? riskPercent}) async {
    _risk = _risk.copyWith(deposit: deposit, riskPercent: riskPercent);
    return _risk;
  }
}
