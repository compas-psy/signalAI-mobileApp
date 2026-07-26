import '../domain/analysis/backtester.dart';
import '../domain/analysis/candle.dart';
import '../domain/analysis/indicators.dart';
import '../domain/analysis/instrument_spec.dart';
import '../domain/analysis/optimizer.dart';
import '../domain/analysis/screener.dart';
import '../domain/enums.dart';
import '../domain/ledger/signal_ledger.dart';
import '../domain/models/digest.dart';
import '../domain/models/portfolio.dart';
import '../domain/models/settings.dart';
import '../domain/models/signal.dart';
import '../domain/models/strategy.dart';
import 'local_store.dart';
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
class LocalAnalysisRepository
    implements SignalAiRepository, ProgressReporting, ParameterOptimizing {
  LocalAnalysisRepository({
    IssClient? iss,
    BybitClient? bybit,
    LocalStore? store,
    this.fortsRoots = const ['SI', 'BR', 'MX', 'RI', 'NG', 'GAZR', 'SBRF'],
    this.cryptoSymbols = const ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'],
    this.maxIdeas = 5,
    this.staleQuoteAfter = const Duration(minutes: 30),
    this.digestFreshFor = const Duration(minutes: 60),
  })  : _iss = iss ?? IssClient(),
        _bybit = bybit ?? BybitClient(),
        _store = store ?? LocalStore();

  final IssClient _iss;
  final BybitClient _bybit;
  final LocalStore _store;

  /// Параметры стратегий: дефолт либо результат walk-forward оптимизации.
  final Map<String, StrategyParams> _params = {
    'forts': StrategyParams.defaults,
    'crypto': StrategyParams.defaults,
  };

  /// Скринер для стратегии — собирается из её текущих параметров.
  Screener _screenerFor(String strategyId) =>
      (_params[strategyId] ?? StrategyParams.defaults).buildScreener();

  /// Скринер FORTS — базовый: его пороги показываются в параметрах.
  Screener get screener => _screenerFor('forts');

  /// Сколько дайджест считается свежим: в течение этого срока повторные
  /// запросы отдают кэш, а не гоняют расчёт заново.
  final Duration digestFreshFor;

  /// Корни фьючерсов вселенной: из них выбирается ближний ликвидный контракт.
  final List<String> fortsRoots;
  final List<String> cryptoSymbols;

  /// Сколько идей показывать (ТЗ §5.4 — не больше пяти на рынок).
  final int maxIdeas;

  /// Насколько старой может быть котировка ISS внутри торговой сессии.
  ///
  /// Проверка работает только когда срочный рынок открыт: вне сессии ISS
  /// держит время последней сделки, и отбраковка по возрасту выбросила бы
  /// вечером и в выходные всю вселенную — а свинг-идеи на 1–5 дней там
  /// по-прежнему осмысленны.
  final Duration staleQuoteAfter;

  /// Причины отбраковки последнего прогона — видны в логе, не теряются молча.
  final List<RejectedCandidate> lastRejections = [];

  /// Слушатель стадий расчёта: интерфейс показывает живой прогресс,
  /// а не зависшую заставку.
  @override
  void Function(String stage)? onProgress;

  void _stage(String stage) => onProgress?.call(stage);

  bool _loaded = false;

  /// Вечный журнал сигналов: форвард-статистика стратегии на реальных свечах.
  final SignalLedger ledger = SignalLedger();

  DailyDigest? _cachedDigest;
  DateTime? _digestAt;
  DateTime? _optimizedAt;
  String? _optimizationNote;

  /// Ленивая загрузка сохранённого состояния — один раз на процесс.
  Future<void> _ensureLoaded() async {
    if (_loaded) return;
    _loaded = true;

    final state = await _store.read('state');
    if (state != null) {
      try {
        final risk = state['risk'] as Map<String, dynamic>?;
        if (risk != null) {
          _risk = _risk.copyWith(
            deposit: (risk['deposit'] as num?)?.toDouble(),
            riskPercent: (risk['risk_percent'] as num?)?.toDouble(),
          );
        }
        void mergeBools(String key, Map<String, bool> into) {
          final saved = state[key] as Map<String, dynamic>?;
          if (saved == null) return;
          for (final entry in saved.entries) {
            if (entry.value is bool) into[entry.key] = entry.value as bool;
          }
        }

        final strategies = <String, bool>{..._strategyEnabled};
        mergeBools('strategy_enabled', strategies);
        _strategyEnabled = strategies;
        final channels = <String, bool>{..._channels};
        mergeBools('channels', channels);
        _channels = channels;
        final notifications = <String, bool>{..._notifications};
        mergeBools('notifications', notifications);
        _notifications = notifications;

        final params = state['params'] as Map<String, dynamic>?;
        if (params != null) {
          for (final entry in params.entries) {
            _params[entry.key] =
                StrategyParams.fromJson(entry.value as Map<String, dynamic>);
          }
        }
        _optimizedAt = DateTime.tryParse(state['optimized_at'] as String? ?? '');
        _optimizationNote = state['optimization_note'] as String?;

        final backtests = state['backtests'] as Map<String, dynamic>?;
        if (backtests != null) {
          for (final entry in backtests.entries) {
            _lastBacktests[entry.key] =
                BacktestResult.fromJson(entry.value as Map<String, dynamic>);
          }
        }
      } on Exception {
        // Битое состояние не должно ломать запуск — работаем с дефолтами.
      }
    }

    final storedLedger = await _store.read('ledger');
    if (storedLedger != null) {
      try {
        final parsed = SignalLedger.fromJson(storedLedger);
        ledger.trades.addAll(parsed.trades);
        ledger.rejected.addAll(parsed.rejected);
      } on Exception {
        // Битый журнал не должен ломать запуск.
      }
    }

    final cached = await _store.read('digest');
    if (cached != null) {
      try {
        _digestAt = DateTime.tryParse(cached['at'] as String? ?? '');
        _cachedDigest =
            DailyDigest.fromJson(cached['digest'] as Map<String, dynamic>);
      } on Exception {
        _cachedDigest = null;
      }
    }
  }

  Future<void> _persistState() => _store.write('state', {
        'risk': {'deposit': _risk.deposit, 'risk_percent': _risk.riskPercent},
        'strategy_enabled': _strategyEnabled,
        'channels': _channels,
        'notifications': _notifications,
        'params': {
          for (final entry in _params.entries) entry.key: entry.value.toJson(),
        },
        'optimized_at': _optimizedAt?.toIso8601String(),
        'optimization_note': _optimizationNote,
        'backtests': {
          for (final entry in _lastBacktests.entries)
            entry.key: entry.value.toJson(),
        },
      });

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

  /// Возраст последнего расчёта; null — расчёта ещё не было.
  Duration? get digestAge =>
      _digestAt == null ? null : DateTime.now().difference(_digestAt!);

  @override
  Future<DailyDigest> fetchDigest({bool force = false}) async {
    await _ensureLoaded();

    // Свежий кэш отдаётся сразу: переключение вкладок и перезапуск приложения
    // не повод пересчитывать рынок. Пересчёт — по кнопке, по часу или force.
    final cached = _cachedDigest;
    final age = digestAge;
    if (!force && cached != null && age != null && age < digestFreshFor) {
      return cached;
    }

    lastRejections.clear();

    _stage('Снимок срочного рынка MOEX…');
    final snapshots = await _iss.fortsSnapshot();
    final selected = _selectContracts(snapshots);
    final usdRub = _usdRub(selected);

    _stage('Режим рынка: индекс, валюта, биткоин…');
    final regime = await _regime(selected);
    final results = <ScreenerResult>[];

    // Часовики каждого инструмента прогона — ими же сверяется журнал сигналов.
    final hourlyBySymbol = <String, List<Candle>>{};

    if (_strategyEnabled['forts'] ?? true) {
      final fortsScreener = _screenerFor('forts');
      for (final snapshot in selected.values) {
        _stage('Анализ ${snapshot.spec.symbol}…');
        final input = await _fortsInput(snapshot);
        if (input == null) continue;
        hourlyBySymbol[snapshot.spec.symbol] = input.hourly;
        final result = fortsScreener.evaluate(input, regime, rejected: lastRejections);
        if (result != null) results.add(result);
      }
    }

    if (_strategyEnabled['crypto'] ?? true) {
      results.addAll(await _cryptoResults(regime, usdRub, hourlyBySymbol));
    }

    results.sort((a, b) => b.signal.score.compareTo(a.signal.score));

    // Дисциплина корреляций (ТЗ §1: «сделки не толкаются плечами»): из каждой
    // группы — только лучшая идея. Два лонга Si и CNY — это одна ставка на
    // рубль двойным размером, а не две идеи.
    final signals = <TradingSignal>[];
    final takenGroups = <String>{};
    for (final r in results) {
      final group = r.signal.correlationGroup;
      if (group != null && !takenGroups.add(group)) {
        lastRejections.add(RejectedCandidate(
          r.signal.symbol,
          'коррелирует с ${signals.firstWhere((s) => s.correlationGroup == group).symbol} — берём лучшую из группы',
          price: r.signal.entry,
        ));
        continue;
      }
      signals.add(r.signal);
      if (signals.length >= maxIdeas) break;
    }
    final now = DateTime.now();

    final digest = DailyDigest(
      title: 'Идеи на сегодня',
      subtitle: '${_dateLabel(now)} · расчёт на устройстве',
      deliveryBadges: const [],
      regime: _regimeQuotes(selected, regime),
      regimeNote: _regimeNote(regime),
      events: const [],
      signals: signals,
      signalsQuota: '${signals.length} из $maxIdeas',
      sourceNote: 'Идеи и уровни рассчитаны на этом устройстве в '
          '${_timeLabel(now)} по котировкам MOEX ISS и публичного Bybit. '
          'Это не демо-данные: график в карточке — те же свечи, по которым '
          'считался сигнал.',
      rejections: [
        for (final r in lastRejections) '${r.symbol} — ${r.reason}',
      ],
    );

    // Журнал: новые сигналы записываются, живые — проживаются вперёд по тем
    // же свечам, отбраковки получают цену для форвард-проверки.
    for (final signal in signals) {
      ledger.record(signal, now);
    }
    for (final r in lastRejections) {
      final price = r.price;
      if (price != null) ledger.recordRejection(r.symbol, r.reason, price, now);
    }
    await _reconcileLedger(hourlyBySymbol);

    _cachedDigest = digest;
    _digestAt = now;
    await _store.write('digest', {
      'at': now.toIso8601String(),
      'digest': digest.toJson(),
    });
    return digest;
  }

  /// Сверка журнала: живым записям без свежих свечей (символ выпал из
  /// вселенной) свечи дотягиваются отдельно.
  Future<void> _reconcileLedger(Map<String, List<Candle>> hourlyBySymbol) async {
    for (final trade in ledger.openOrPending) {
      if (hourlyBySymbol.containsKey(trade.symbol)) continue;
      try {
        final crypto = trade.strategyId == 'crypto';
        hourlyBySymbol[trade.symbol] = crypto
            ? await _bybit.candles(trade.symbol, timeframe: Timeframe.h1)
            : await _iss.candles(
                trade.symbol,
                timeframe: Timeframe.h1,
                from: trade.createdAt.subtract(const Duration(days: 1)),
              );
      } on Exception {
        continue; // нет свечей — сверим в следующий раз
      }
    }
    ledger.reconcile(hourlyBySymbol);
    await _store.write('ledger', ledger.toJson());
  }

  /// Ближний контракт по каждому корню: не ближе трёх дней до экспирации
  /// и с наибольшим оборотом (ТЗ §5.4).
  Map<String, FortsSnapshot> _selectContracts(List<FortsSnapshot> snapshots) {
    final now = DateTime.now();
    final sessionOpen = _isFortsSessionOpen(IssClient.mskNow());
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

      // Внутри сессии котировка обязана быть свежей: торгуем по цене, а не по
      // следу вчерашних торгов.
      if (sessionOpen) {
        final age = snapshot.ageAt(now);
        if (age != null && age > staleQuoteAfter) {
          lastRejections.add(
            RejectedCandidate(
              symbol,
              'котировка старше ${age.inMinutes} мин.',
              price: snapshot.lastPrice,
            ),
          );
          continue;
        }
      }
      final current = byRoot[root];
      if (current == null || snapshot.turnover > current.turnover) {
        byRoot[root] = snapshot;
      }
    }
    return byRoot;
  }

  /// Идут ли торги на срочном рынке. [mskNow] — московское время.
  ///
  /// Грубо: будни с 09:00 до 23:50. Клиринговые перерывы сюда не заводятся —
  /// они короче порога свежести, и лишний раз дробить окно незачем.
  static bool _isFortsSessionOpen(DateTime mskNow) {
    if (mskNow.weekday > DateTime.friday) return false;
    final minutes = mskNow.hour * 60 + mskNow.minute;
    return minutes >= 9 * 60 && minutes < 23 * 60 + 50;
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

  Future<List<ScreenerResult>> _cryptoResults(
    MarketRegime regime,
    double usdRub,
    Map<String, List<Candle>> hourlyBySymbol,
  ) async {
    final results = <ScreenerResult>[];
    _stage('Bybit: тикеры…');
    final tickers = await _bybit.tickers(symbols: cryptoSymbols);

    for (final ticker in tickers) {
      _stage('Анализ ${ticker.symbol}…');
      try {
        final hourly = await _bybit.candles(ticker.symbol, timeframe: Timeframe.h1);
        hourlyBySymbol[ticker.symbol] = hourly;
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

  /// Экран «Сделки» — форвард-статистика журнала сигналов.
  ///
  /// Это не бэктест: каждая строка — сигнал, записанный в момент выдачи и
  /// прожитый вперёд по реальным свечам. Подделать эту статистику нельзя —
  /// поэтому именно она отвечает на вопрос «стратегия вообще работает?».
  @override
  Future<TradesSummary> fetchTrades() async {
    await _ensureLoaded();
    final closed = ledger.closed;
    final live = ledger.openOrPending;

    if (closed.isEmpty && live.isEmpty) {
      return const TradesSummary(
        equityTitle: 'Эквити · журнал пуст',
        equityChange: 'бумажная торговля',
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
    }

    final pf = ledger.profitFactor;
    final total = ledger.totalR;
    String r(double v) =>
        '${v >= 0 ? '+' : '−'}${v.abs().toStringAsFixed(2).replaceAll('.', ',')}R';

    final rejectedMove = ledger.rejectedAverageMove24h;
    return TradesSummary(
      equityTitle: 'Эквити · бумажная торговля (журнал сигналов)',
      equityChange: closed.isEmpty ? 'сделок ещё нет' : r(total),
      equityCurve: ledger.equityCurve,
      stats: [
        StatTile(
          value: closed.isEmpty ? '—' : '${ledger.winRate.round()}%',
          label: 'винрейт',
        ),
        StatTile(
          value: closed.isEmpty ? '—' : r(ledger.averageR),
          label: 'ср. сделка',
          tone: ledger.averageR > 0 ? Tone.positive : Tone.negative,
        ),
        StatTile(
          value: closed.isEmpty
              ? '—'
              : pf == null
                  ? '∞'
                  : pf.toStringAsFixed(1).replaceAll('.', ','),
          label: 'профит-фактор',
        ),
        StatTile(
          value: rejectedMove == null
              ? '${closed.length}'
              : '${closed.length} · отбр. ${rejectedMove >= 0 ? '+' : '−'}'
                  '${rejectedMove.abs().toStringAsFixed(1).replaceAll('.', ',')}%',
          label: rejectedMove == null ? 'сделок' : 'сделок · ход отбр./24ч',
        ),
      ],
      positions: [
        for (final t in live)
          ActivePosition(
            symbol: t.symbol,
            direction: t.long ? Direction.long : Direction.short,
            entryLabel: t.entry.toStringAsFixed(2).replaceAll('.', ','),
            currentLabel: t.status == PaperStatus.pending ? 'лимитка' : 'в позиции',
            pnlLabel: t.status == PaperStatus.pending
                ? 'ждёт входа'
                : r(t.unrealizedR ?? 0),
            pnlPositive: (t.unrealizedR ?? 0) >= 0,
            progressPercent: t.status == PaperStatus.pending
                ? 0
                : (t.tpsTaken * 100 ~/ (t.tpPrices.isEmpty ? 1 : t.tpPrices.length))
                    .clamp(0, 100),
            stage: t.status == PaperStatus.pending
                ? 'бумажная · лимитка живёт 24 часа'
                : 'бумажная · взято тейков: ${t.tpsTaken} из ${t.tpPrices.length}',
          ),
      ],
      journal: [
        for (final t in closed.reversed.take(30))
          JournalEntry(
            date: t.closedAt == null
                ? ''
                : '${t.closedAt!.day.toString().padLeft(2, '0')}.'
                    '${t.closedAt!.month.toString().padLeft(2, '0')}',
            symbol: t.symbol,
            direction: t.long ? Direction.long : Direction.short,
            outcome: t.outcome ?? '—',
            rMultiple: t.resultR ?? 0,
          ),
      ],
    );
  }

  @override
  Future<StrategiesSnapshot> fetchStrategies() async {
    await _ensureLoaded();
    return StrategiesSnapshot(
        packs: [
          StrategyPack(
            id: 'forts',
            name: 'Интеграционная · MOEX FORTS',
            description: 'Структура и BOS/CHoCH, зона входа, Price Action, RSI, '
                'объём. Данные — MOEX ISS, расчёт на устройстве.',
            statsLabel: 'вселенная: ${fortsRoots.join(' · ')}\n'
                '${(_params['forts'] ?? StrategyParams.defaults).label}',
            enabled: _strategyEnabled['forts'] ?? true,
          ),
          StrategyPack(
            id: 'crypto',
            name: 'Crypto SMC · Bybit',
            description: 'То же ядро плюс фандинг и дельта открытого интереса. '
                'Публичные данные Bybit, ключ не нужен.',
            statsLabel: 'вселенная: ${cryptoSymbols.join(' · ')}\n'
                '${(_params['crypto'] ?? StrategyParams.defaults).label}',
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
            value: screener.takeProfitMultiples.map((m) => '${_num(m)}R').join(' / '),
          ),
          StrategyParam(
            name: 'Оптимизация',
            value: _optimizedAt == null
                ? 'walk-forward · ещё не выполнялась'
                : 'walk-forward · ${_optimizedAt!.day.toString().padLeft(2, '0')}.'
                    '${_optimizedAt!.month.toString().padLeft(2, '0')}'
                    '${_optimizationNote == null ? '' : ' · $_optimizationNote'}',
          ),
        ],
        backtest: _lastBacktests['forts'] ??
            _lastBacktests['crypto'] ??
            const BacktestResult(
              info: 'прогона ещё не было — запустите',
              stats: [],
              equityCurve: [],
            ),
      );
  }

  @override
  Future<SettingsSnapshot> fetchSettings() async {
    await _ensureLoaded();
    return SettingsSnapshot(
        exchanges: const [
          // Источники данных: отсюда приходят котировки и свечи. «Активно»
          // здесь не значит «можно торговать» — торговый доступ ниже.
          ExchangeAccount(
            id: 'moex-iss',
            abbr: 'M',
            name: 'MOEX ISS',
            subtitle: 'Котировки и свечи срочного рынка · ключ не нужен',
            connected: true,
            accentHex: 0xFFFFD400,
            isDataSource: true,
          ),
          ExchangeAccount(
            id: 'bybit-public',
            abbr: 'B',
            name: 'Bybit · публичный API',
            subtitle: 'Свечи, открытый интерес, фандинг · ключ не нужен',
            connected: true,
            accentHex: 0xFFF7A600,
            isDataSource: true,
          ),
          ExchangeAccount(
            id: 'tinvest',
            abbr: 'T',
            name: 'Т-Инвестиции API',
            subtitle: 'Исполнение сделок пока не реализовано в приложении',
            connected: false,
            accentHex: 0xFF8E8E98,
          ),
          ExchangeAccount(
            id: 'bybit-trade',
            abbr: 'B',
            name: 'Bybit · торговый доступ',
            subtitle: 'Исполнение сделок пока не реализовано в приложении',
            connected: false,
            accentHex: 0xFF8E8E98,
          ),
        ],
        channels: [
          ToggleSetting(
            id: 'push',
            name: 'Пуш-уведомления',
            subtitle: 'Новый сигнал с оценкой 75+ — уведомление на устройстве. '
                'Работает, пока приложение запущено (хотя бы в фоне)',
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
            name: 'Автопересчёт раз в час',
            subtitle: 'Пока приложение запущено, идеи пересчитываются каждый час '
                'и сверяются с рынком',
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
  }

  @override
  Future<void> confirmSignal(String signalId) async {
    // Исполнение появится вместе с брокерским адаптером и ключами в Keystore.
    throw const FeatureUnavailableException(
      'Исполнение сделок ещё не реализовано: приложение пока только '
      'анализирует рынок. Брокерский адаптер — следующий шаг разработки.',
    );
  }

  @override
  Future<void> setStrategyEnabled(String strategyId, bool enabled) async {
    await _ensureLoaded();
    _strategyEnabled = {..._strategyEnabled, strategyId: enabled};
    await _persistState();
  }

  /// Прогон стратегии по реальной истории на устройстве.
  ///
  /// Даты правил и допущения прогона — в [Backtester]; итоговая подпись
  /// перечисляет их честно, а не прячет.
  @override
  Future<BacktestResult> runBacktest(String strategyId) async {
    final histories = strategyId == 'crypto'
        ? await _cryptoHistories()
        : await _fortsHistories();
    if (histories.isEmpty) {
      throw const FeatureUnavailableException(
        'История не получена: биржа не отдала свечи. Попробуйте позже.',
      );
    }

    final summary = await Backtester(screener: _screenerFor(strategyId))
        .run(histories, onProgress: onProgress);
    final backtest = _lastBacktests[strategyId] = _formatBacktest(summary);
    await _persistState();
    return backtest;
  }

  // ── Walk-forward оптимизация ───────────────────────────────────────────

  /// Пора ли пересчитывать параметры. Раз в неделю: чаще для свинга 1–5 дней
  /// означает подгонку под последние колебания — между ежедневными прогонами
  /// накапливается слишком мало новых сделок (обоснование в [StrategyOptimizer]).
  @override
  bool get optimizationDue =>
      _optimizedAt == null ||
      DateTime.now().difference(_optimizedAt!) > const Duration(days: 7);

  @override
  Future<String> optimizeParameters() async {
    await _ensureLoaded();
    const optimizer = StrategyOptimizer();
    final notes = <String>[];

    for (final strategyId in ['forts', 'crypto']) {
      if (!(_strategyEnabled[strategyId] ?? true)) continue;
      _stage('Оптимизация $strategyId: история…');
      final histories = strategyId == 'crypto'
          ? await _cryptoHistories()
          : await _fortsHistories();
      if (histories.isEmpty) continue;

      final outcome = await optimizer.optimize(
        histories,
        onProgress: (stage) => _stage('$strategyId: $stage'),
      );
      _params[strategyId] = outcome.params;
      final pf = outcome.testProfitFactor;
      notes.add(outcome.improved
          ? '$strategyId: новые параметры, PF ${pf == null ? '∞' : pf.toStringAsFixed(1).replaceAll('.', ',')} '
              'на ${outcome.testTrades} сделках вне выборки'
          : '$strategyId: дефолт не обыгран — параметры не тронуты');
    }

    _optimizedAt = DateTime.now();
    _optimizationNote = notes.isEmpty ? null : notes.join(' · ');
    await _persistState();
    return notes.isEmpty
        ? 'Оптимизация пропущена: нет истории'
        : notes.join(' · ');
  }

  /// Итоги последних прогонов — чтобы карточка бэктеста переживала уход
  /// с экрана «Стратегии» и возврат на него.
  final Map<String, BacktestResult> _lastBacktests = {};

  Future<List<InstrumentHistory>> _fortsHistories() async {
    _stage('MOEX: снимок рынка…');
    final snapshots = await _iss.fortsSnapshot();
    final selected = _selectContracts(snapshots);
    final histories = <InstrumentHistory>[];
    for (final snapshot in selected.values) {
      final symbol = snapshot.spec.symbol;
      _stage('История $symbol…');
      try {
        final hourly = await _iss.candles(
          symbol,
          timeframe: Timeframe.h1,
          from: DateTime.now().subtract(const Duration(days: 60)),
        );
        final daily = await _iss.candles(
          symbol,
          timeframe: Timeframe.d1,
          from: DateTime.now().subtract(const Duration(days: 180)),
        );
        histories.add(InstrumentHistory(spec: snapshot.spec, hourly: hourly, daily: daily));
      } on Exception {
        // Инструмент без истории просто не участвует в прогоне.
        continue;
      }
    }
    return histories;
  }

  Future<List<InstrumentHistory>> _cryptoHistories() async {
    _stage('Bybit: тикеры…');
    final tickers = await _bybit.tickers(symbols: cryptoSymbols);
    final usdRub = 90.0;
    final histories = <InstrumentHistory>[];
    for (final ticker in tickers) {
      _stage('История ${ticker.symbol}…');
      try {
        final hourly = await _bybit.candles(ticker.symbol, timeframe: Timeframe.h1, limit: 1000);
        final daily = await _bybit.candles(ticker.symbol, timeframe: Timeframe.d1, limit: 200);
        histories.add(
          InstrumentHistory(
            spec: InstrumentSpec(
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
            ),
            hourly: hourly,
            daily: daily,
          ),
        );
      } on Exception {
        continue;
      }
    }
    return histories;
  }

  BacktestResult _formatBacktest(BacktestSummary summary) {
    final pf = summary.profitFactor;
    return BacktestResult(
      info: '${summary.days} дн · ${summary.instruments} инстр. · на устройстве · '
          'без комиссий · режим рынка нейтрален',
      stats: [
        StatTile(
          value: summary.count == 0 ? '—' : '${summary.winRate.round()}%',
          label: 'винрейт',
        ),
        StatTile(
          value: summary.count == 0
              ? '—'
              : '${summary.averageR >= 0 ? '+' : '−'}'
                  '${summary.averageR.abs().toStringAsFixed(2).replaceAll('.', ',')}R',
          label: 'ср. сделка',
        ),
        StatTile(
          value: summary.count == 0
              ? '—'
              : pf == null
                  ? '∞'
                  : pf.toStringAsFixed(1).replaceAll('.', ','),
          label: 'профит-фактор',
        ),
        StatTile(value: '${summary.count}', label: 'сделок'),
      ],
      equityCurve: summary.equityCurve,
    );
  }

  @override
  Future<ExchangeAccount> connectExchange(String exchangeId) async =>
      throw const FeatureUnavailableException(
        'Торговый доступ ещё не реализован: ввод ключей появится вместе с '
        'исполнением сделок. Для анализа ключи не нужны — данные уже идут.',
      );

  @override
  Future<void> setChannelEnabled(String channelId, bool enabled) async {
    await _ensureLoaded();
    _channels = {..._channels, channelId: enabled};
    await _persistState();
  }

  @override
  Future<void> setNotificationEnabled(String notificationId, bool enabled) async {
    await _ensureLoaded();
    _notifications = {..._notifications, notificationId: enabled};
    await _persistState();
  }

  @override
  Future<RiskProfile> updateRiskProfile({double? deposit, double? riskPercent}) async {
    await _ensureLoaded();
    _risk = _risk.copyWith(deposit: deposit, riskPercent: riskPercent);
    await _persistState();
    return _risk;
  }

  /// Включён ли локальный пуш о новых сигналах.
  bool get pushEnabled => _channels['push'] ?? false;

  /// Включён ли часовой автопересчёт.
  bool get autoRefreshEnabled => _notifications['digest'] ?? false;

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

  static String _timeLabel(DateTime now) =>
      '${now.hour.toString().padLeft(2, '0')}:${now.minute.toString().padLeft(2, '0')}';

  static String _dateLabel(DateTime now) =>
      '${_weekdays[now.weekday - 1]}, ${now.day} ${_months[now.month - 1]} · ${_timeLabel(now)}';

  /// Ограничение автономного режима: без сервера нет ни расписания, ни
  /// круглосуточного присмотра за позициями.
  static const limitationsNote =
      'Автономный режим: анализ выполняется при открытии приложения. '
      'Дайджест по расписанию, сопровождение позиций и доставка в Telegram/MAX '
      'требуют серверного контура.';
}
