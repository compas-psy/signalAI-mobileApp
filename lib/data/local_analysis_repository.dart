import '../domain/analysis/candle.dart';
import '../domain/analysis/indicators.dart';
import '../domain/analysis/instrument_spec.dart';
import '../domain/analysis/screener.dart';
import '../domain/enums.dart';
import '../domain/models/digest.dart';
import '../domain/models/portfolio.dart';
import '../domain/models/settings.dart';
import '../domain/models/strategy.dart';
import 'market/bybit_client.dart';
import 'market/iss_client.dart';
import 'repository.dart';

/// Автономный режим: анализ считается на устройстве по публичным данным.
///
/// MOEX ISS и публичный API Bybit не требуют ключей (ТЗ §3), поэтому идеи
/// появляются без сервера и без единого секрета на устройстве. Ограничения
/// режима честные: расчёт идёт в момент открытия приложения, а не по
/// расписанию 10:10, и позиции не сопровождаются, пока приложение закрыто —
/// для этого нужен серверный контур из ТЗ §2.
class LocalAnalysisRepository implements SignalAiRepository {
  LocalAnalysisRepository({
    IssClient? iss,
    BybitClient? bybit,
    this.screener = const Screener(),
    this.fortsRoots = const ['SI', 'BR', 'MX', 'RI', 'NG', 'GAZR', 'SBRF'],
    this.cryptoSymbols = const ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
    this.maxIdeas = 5,
  })  : _iss = iss ?? IssClient(),
        _bybit = bybit ?? BybitClient();

  final IssClient _iss;
  final BybitClient _bybit;
  final Screener screener;

  /// Корни фьючерсов вселенной: из них выбирается ближний ликвидный контракт.
  final List<String> fortsRoots;
  final List<String> cryptoSymbols;

  /// Сколько идей показывать (ТЗ §5.4 — не больше пяти на рынок).
  final int maxIdeas;

  /// Причины отбраковки последнего прогона — видны в логе, не теряются молча.
  final List<RejectedCandidate> lastRejections = [];

  RiskProfile _risk = const RiskProfile(
    deposit: 1000000,
    riskPercent: 0.75,
    dailyLossLimit: '−2% · автостоп',
    maxConcurrentTrades: 'до 3',
    pauseRule: 'пауза до завтра',
  );
  Map<String, bool> _strategyEnabled = {'forts': true, 'crypto': true};
  Map<String, bool> _channels = {'push': false, 'telegram': false, 'max': false};
  Map<String, bool> _notifications = {'digest': false, 'alerts': false, 'events': false};

  @override
  Future<DailyDigest> fetchDigest() async {
    lastRejections.clear();

    final snapshots = await _iss.fortsSnapshot();
    final selected = _selectContracts(snapshots);
    final usdRub = _usdRub(selected);

    final regime = await _regime(selected);
    final results = <ScreenerResult>[];

    for (final snapshot in selected.values) {
      final input = await _fortsInput(snapshot);
      if (input == null) continue;
      final result = screener.evaluate(input, regime, rejected: lastRejections);
      if (result != null) results.add(result);
    }

    if (_strategyEnabled['crypto'] ?? true) {
      results.addAll(await _cryptoResults(regime, usdRub));
    }

    results.sort((a, b) => b.signal.score.compareTo(a.signal.score));
    final signals = [for (final r in results.take(maxIdeas)) r.signal];

    return DailyDigest(
      title: 'Идеи на сегодня',
      subtitle: '${_dateLabel(DateTime.now())} · расчёт на устройстве',
      deliveryBadges: const [],
      regime: _regimeQuotes(selected, regime),
      regimeNote: _regimeNote(regime),
      events: const [],
      signals: signals,
      signalsQuota: '${signals.length} из $maxIdeas',
    );
  }

  /// Ближний контракт по каждому корню: не ближе трёх дней до экспирации
  /// и с наибольшим оборотом (ТЗ §5.4).
  Map<String, FortsSnapshot> _selectContracts(List<FortsSnapshot> snapshots) {
    final now = DateTime.now();
    final byRoot = <String, FortsSnapshot>{};

    for (final snapshot in snapshots) {
      final symbol = snapshot.spec.symbol.toUpperCase();
      final root = fortsRoots.firstWhere(
        (r) => symbol.startsWith(r),
        orElse: () => '',
      );
      if (root.isEmpty) continue;

      final expiration = snapshot.spec.expiration;
      if (expiration != null && expiration.difference(now).inDays < screener.minDaysToExpiration) {
        continue;
      }
      final current = byRoot[root];
      if (current == null || snapshot.turnover > current.turnover) {
        byRoot[root] = snapshot;
      }
    }
    return byRoot;
  }

  /// Курс доллара из фьючерса Si (котируется в рублях за 1000 долларов).
  double _usdRub(Map<String, FortsSnapshot> selected) {
    final si = selected['SI'];
    if (si == null || si.lastPrice <= 0) return 90;
    return si.lastPrice / 1000;
  }

  Future<InstrumentInput?> _fortsInput(FortsSnapshot snapshot) async {
    try {
      final hourly = await _iss.candles(
        snapshot.spec.symbol,
        timeframe: Timeframe.h1,
        from: DateTime.now().subtract(const Duration(days: 14)),
      );
      final daily = await _iss.candles(
        snapshot.spec.symbol,
        timeframe: Timeframe.d1,
        from: DateTime.now().subtract(const Duration(days: 90)),
      );
      return InstrumentInput(
        spec: snapshot.spec,
        hourly: hourly,
        daily: daily,
        lastPrice: snapshot.lastPrice,
        changePercentLabel: _percentLabel(snapshot.changePercent),
        changeUp: snapshot.changePercent >= 0,
      );
    } on Exception catch (e) {
      lastRejections.add(RejectedCandidate(snapshot.spec.symbol, 'нет свечей: $e'));
      return null;
    }
  }

  Future<List<ScreenerResult>> _cryptoResults(MarketRegime regime, double usdRub) async {
    final results = <ScreenerResult>[];
    final tickers = await _bybit.tickers(symbols: cryptoSymbols);

    for (final ticker in tickers) {
      try {
        final hourly = await _bybit.candles(ticker.symbol, timeframe: Timeframe.h1);
        final daily = await _bybit.candles(ticker.symbol, timeframe: Timeframe.d1, limit: 120);
        final oiHistory = await _bybit.openInterestHistory(ticker.symbol);
        final oiChange = oiHistory.length < 24 || oiHistory.first == 0
            ? null
            : (oiHistory.last - oiHistory.first) / oiHistory.first * 100;

        // Единица объёма для крипты — 0,01 монеты; риск считается в рублях.
        final spec = InstrumentSpec(
          id: ticker.symbol.toLowerCase(),
          symbol: ticker.symbol,
          name: 'Bybit · перпетуал',
          market: Market.crypto,
          priceDecimals: ticker.lastPrice >= 1000 ? 0 : 2,
          valuePerPoint: 0.01 * usdRub,
          unitMultiplier: 0.01,
          unitDecimals: 2,
          unitName: ticker.symbol.replaceAll('USDT', ''),
          unitRiskSuffix: '0,01 ${ticker.symbol.replaceAll('USDT', '')}',
        );

        final result = screener.evaluate(
          InstrumentInput(
            spec: spec,
            hourly: hourly,
            daily: daily,
            lastPrice: ticker.lastPrice,
            changePercentLabel: _percentLabel(ticker.changePercent),
            changeUp: ticker.changePercent >= 0,
            openInterestChangePercent: oiChange,
            fundingRate: ticker.fundingRate,
          ),
          regime,
          rejected: lastRejections,
        );
        if (result != null) results.add(result);
      } on Exception catch (e) {
        lastRejections.add(RejectedCandidate(ticker.symbol, 'нет данных: $e'));
      }
    }
    return results;
  }

  /// Режим рынка по дневной структуре индекса, валюты и биткоина (ТЗ §5.1).
  Future<MarketRegime> _regime(Map<String, FortsSnapshot> selected) async {
    Future<StructureTrend> trendOf(FortsSnapshot? snapshot) async {
      if (snapshot == null) return StructureTrend.flat;
      try {
        final daily = await _iss.candles(
          snapshot.spec.symbol,
          timeframe: Timeframe.d1,
          from: DateTime.now().subtract(const Duration(days: 90)),
        );
        if (daily.length < 20) return StructureTrend.flat;
        return analyzeStructure(daily).trend;
      } on Exception {
        return StructureTrend.flat;
      }
    }

    var crypto = StructureTrend.flat;
    try {
      final btc = await _bybit.candles('BTCUSDT', timeframe: Timeframe.d1, limit: 90);
      if (btc.length >= 20) crypto = analyzeStructure(btc).trend;
    } on Exception {
      crypto = StructureTrend.flat;
    }

    return MarketRegime(
      indexTrend: await trendOf(selected['MX'] ?? selected['RI']),
      currencyTrend: await trendOf(selected['SI']),
      cryptoTrend: crypto,
    );
  }

  List<RegimeQuote> _regimeQuotes(
    Map<String, FortsSnapshot> selected,
    MarketRegime regime,
  ) =>
      [
        for (final entry in selected.entries)
          RegimeQuote(
            name: entry.key,
            value: _percentLabel(entry.value.changePercent),
            tone: entry.value.changePercent >= 0 ? Tone.positive : Tone.negative,
          ),
      ];

  String _regimeNote(MarketRegime regime) {
    String word(StructureTrend t) => switch (t) {
          StructureTrend.up => 'растёт',
          StructureTrend.down => 'падает',
          StructureTrend.flat => 'во флэте',
        };
    return 'Индекс ${word(regime.indexTrend)}, валюта ${word(regime.currencyTrend)}, '
        'крипта ${word(regime.cryptoTrend)}. Идеи против режима рынка теряют '
        'блок оценки и обычно не проходят порог.';
  }

  @override
  Future<TradesSummary> fetchTrades() async => const TradesSummary(
        equityTitle: 'Эквити · сделок нет',
        equityChange: '—',
        equityCurve: [],
        stats: [
          StatTile(value: '—', label: 'винрейт'),
          StatTile(value: '—', label: 'ср. сделка'),
          StatTile(value: '—', label: 'профит-фактор'),
          StatTile(value: '0', label: 'сделок'),
        ],
        positions: [],
        journal: [],
      );

  @override
  Future<StrategiesSnapshot> fetchStrategies() async => StrategiesSnapshot(
        packs: [
          StrategyPack(
            id: 'forts',
            name: 'Интеграционная · MOEX FORTS',
            description: 'Структура и BOS/CHoCH, зона входа, Price Action, RSI, '
                'объём. Данные — MOEX ISS, расчёт на устройстве.',
            statsLabel: 'вселенная: ${fortsRoots.join(' · ')}',
            enabled: _strategyEnabled['forts'] ?? true,
          ),
          StrategyPack(
            id: 'crypto',
            name: 'Crypto SMC · Bybit',
            description: 'То же ядро плюс фандинг и дельта открытого интереса. '
                'Публичные данные Bybit, ключ не нужен.',
            statsLabel: 'вселенная: ${cryptoSymbols.join(' · ')}',
            enabled: _strategyEnabled['crypto'] ?? true,
          ),
        ],
        paramsTitle: 'Параметры скринера',
        params: [
          StrategyParam(
            name: 'Риск на сделку',
            value: '${_percentText(_risk.riskPercent)} депозита',
          ),
          StrategyParam(name: 'Мин. R:R до TP2', value: _num(screener.minRiskRewardToTp2)),
          StrategyParam(name: 'Мин. SignalScore', value: '${screener.minScore}'),
          StrategyParam(name: 'Идей в день', value: 'до $maxIdeas'),
          StrategyParam(
            name: 'Фильтр экспирации',
            value: 'больше ${screener.minDaysToExpiration} торговых дней',
          ),
          const StrategyParam(name: 'Таймфреймы', value: '1H — структура · 1D — контекст'),
          StrategyParam(
            name: 'Тейки',
            value: Screener.takeProfitMultiples.map((m) => '${_num(m)}R').join(' / '),
          ),
        ],
        backtest: const BacktestResult(
          info: 'локальный прогон пока не реализован',
          stats: [],
          equityCurve: [],
        ),
      );

  @override
  Future<SettingsSnapshot> fetchSettings() async => SettingsSnapshot(
        exchanges: const [
          ExchangeAccount(
            id: 'moex-iss',
            abbr: 'M',
            name: 'MOEX ISS',
            subtitle: 'Публичные данные срочного рынка · ключ не нужен',
            connected: true,
            accentHex: 0xFFFFD400,
          ),
          ExchangeAccount(
            id: 'bybit-public',
            abbr: 'B',
            name: 'Bybit',
            subtitle: 'Публичные свечи, OI и фандинг · ключ не нужен',
            connected: true,
            accentHex: 0xFFF7A600,
          ),
          ExchangeAccount(
            id: 'tinvest',
            abbr: 'T',
            name: 'Т-Инвестиции API',
            subtitle: 'Нужен для остатков и исполнения',
            connected: false,
            accentHex: 0xFF8E8E98,
          ),
        ],
        channels: [
          ToggleSetting(
            id: 'push',
            name: 'Пуш-уведомления',
            subtitle: 'Требуют серверного контура — пока недоступны',
            enabled: _channels['push'] ?? false,
          ),
          ToggleSetting(
            id: 'telegram',
            name: 'Telegram-бот',
            subtitle: 'Требует серверного контура — пока недоступен',
            enabled: _channels['telegram'] ?? false,
          ),
          ToggleSetting(
            id: 'max',
            name: 'MAX',
            subtitle: 'Требует серверного контура — пока недоступен',
            enabled: _channels['max'] ?? false,
          ),
        ],
        notifications: [
          ToggleSetting(
            id: 'digest',
            name: 'Утренний дайджест',
            subtitle: 'В автономном режиме идеи считаются при открытии приложения',
            enabled: _notifications['digest'] ?? false,
          ),
          ToggleSetting(
            id: 'alerts',
            name: 'Срабатывания TP / SL',
            subtitle: 'Появятся вместе с исполнением через брокера',
            enabled: _notifications['alerts'] ?? false,
          ),
          ToggleSetting(
            id: 'events',
            name: 'События по активным идеям',
            subtitle: 'Появятся вместе с календарём событий',
            enabled: _notifications['events'] ?? false,
          ),
        ],
        risk: _risk,
      );

  @override
  Future<void> confirmSignal(String signalId) async {
    // Исполнение появится вместе с брокерским адаптером и ключами в Keystore.
    throw UnimplementedError(
      'Исполнение недоступно: подключите брокера в настройках',
    );
  }

  @override
  Future<void> setStrategyEnabled(String strategyId, bool enabled) async {
    _strategyEnabled = {..._strategyEnabled, strategyId: enabled};
  }

  @override
  Future<BacktestResult> runBacktest(String strategyId) async =>
      throw UnimplementedError('Локальный бэктест пока не реализован');

  @override
  Future<ExchangeAccount> connectExchange(String exchangeId) async =>
      throw UnimplementedError('Ввод ключей появится в следующей версии');

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

  static String _percentLabel(double percent) {
    final sign = percent < 0 ? '−' : '+';
    return '$sign${percent.abs().toStringAsFixed(2).replaceAll('.', ',')}%';
  }

  static String _percentText(double value) =>
      '${_num(value)}%';

  static String _num(double value) {
    final text = value == value.roundToDouble()
        ? value.toStringAsFixed(value.abs() < 10 && value % 1 != 0 ? 1 : 0)
        : value.toString();
    return text.replaceAll('.', ',');
  }

  static const _months = [
    'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
  ];
  static const _weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

  static String _dateLabel(DateTime now) {
    final time = '${now.hour.toString().padLeft(2, '0')}:'
        '${now.minute.toString().padLeft(2, '0')}';
    return '${_weekdays[now.weekday - 1]}, ${now.day} ${_months[now.month - 1]} · $time';
  }

  /// Ограничение автономного режима: без сервера нет ни расписания, ни
  /// круглосуточного присмотра за позициями.
  static const limitationsNote =
      'Автономный режим: анализ выполняется при открытии приложения. '
      'Дайджест по расписанию, сопровождение позиций и доставка в Telegram/MAX '
      'требуют серверного контура.';
}
