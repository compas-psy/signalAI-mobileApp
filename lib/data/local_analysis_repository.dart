import '../domain/analysis/backtester.dart';
import '../domain/broker/broker.dart';
import '../domain/broker/trading_diagnostics.dart';
import '../domain/broker/trading_gate.dart';
import '../domain/analysis/candle.dart';
import '../domain/analysis/indicators.dart';
import '../domain/analysis/instrument_spec.dart';
import '../domain/analysis/factor_audit.dart';
import '../domain/analysis/optimizer.dart';
import '../domain/analysis/screener.dart';
import '../domain/analysis/trend_screener.dart';
import '../domain/analysis/trade_simulator.dart';
import '../domain/enums.dart';
import '../domain/invest/invest_models.dart';
import '../domain/idea/execution.dart';
import '../domain/idea/skip_record.dart';
import '../domain/ledger/signal_ledger.dart';
import '../domain/models/digest.dart';
import '../domain/models/portfolio.dart';
import '../domain/models/settings.dart';
import '../domain/models/signal.dart';
import '../domain/models/trade_plan_geometry.dart';
import '../domain/models/strategy.dart';
import '../domain/risk/risk_engine.dart';
import '../monitor/background_cycle.dart';
import '../monitor/background_mode.dart';
import '../domain/ledger/account.dart';
import '../domain/ledger/ledger_event.dart';
import '../domain/ledger/money.dart';
import '../domain/options/black76.dart';
import '../domain/portfolio/allocation.dart';
import '../domain/portfolio/package_backtest.dart';
import '../domain/portfolio/package_plan.dart';
import '../domain/options/structures.dart';
import 'ledger/broker_import.dart';
import 'ledger/capital_desk.dart';
import 'broker/bybit_broker.dart';
import '../domain/broker/sandbox_proof.dart';
import '../domain/broker/tinvest_role.dart';
import 'broker/tinvest_broker.dart';
import 'broker/tinvest_fundamentals.dart';
import 'broker/secure_vault.dart';
import 'local_store.dart';
import 'state_lock.dart';
import 'market/bybit_client.dart';
import 'market/candle_store.dart';
import 'market/http_json.dart';
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
    implements SignalAiRepository,
        ProgressReporting,
        ParameterOptimizing,
        TradingDesk,
        TradingProbe,
        PaperTracking,
        InvestDesk,
        CapitalKeeper,
        MonitorTarget {
  LocalAnalysisRepository({
    IssClient? iss,
    BybitClient? bybit,
    LocalStore? store,
    this.vault = const SecureVault(),
    // Корни фьючерсной вселенной. Металлы и юань — по просьбе владельца:
    // ликвидные и слабо коррелированные с индексом группы.
    this.fortsRoots = const [
      'SI', 'BR', 'MX', 'RI', 'NG', 'GAZR', 'SBRF', 'GOLD', 'SILV', 'CNY',
    ],
    // Топ-10 перпетуалов Bybit по обороту. Ширина не создаёт эдж — она даёт
    // тому же эджу больше независимых возможностей; хвост списка ликвидности
    // сюда сознательно не берём: проскальзывание там съедает больше, чем
    // даёт сигнал. Группа корреляции у всей крипты одна: альты ходят за
    // биткоином, и риск-движок не даст открыть одну и ту же ставку дважды.
    this.cryptoSymbols = const [
      'BTCUSDT',
      'ETHUSDT',
      'SOLUSDT',
      'XRPUSDT',
      'BNBUSDT',
      'DOGEUSDT',
      'TONUSDT',
      'ADAUSDT',
      'LINKUSDT',
      'AVAXUSDT',
    ],
    this.maxIdeas = 5,
    this.staleQuoteAfter = const Duration(minutes: 30),
    this.digestFreshFor = const Duration(minutes: 60),
  })  : _iss = iss ?? IssClient(),
        _bybit = bybit ?? BybitClient(),
        _store = store ?? LocalStore() {
    // История свечей живёт на диске и докачивается хвостом. Без этого год
    // часовиков по 44 сериям качался бы заново при каждом прогоне.
    final candles = CandleStore(_store);
    _iss.store ??= candles;
    _bybit.store ??= candles;
  }

  final IssClient _iss;
  final BybitClient _bybit;
  final LocalStore _store;

  /// Книга капитала: единственный источник истины по деньгам и позициям.
  late final CapitalDesk _capital = CapitalDesk(store: _store);

  @override
  CapitalDesk get capital => _capital;

  /// Параметры стратегий: дефолт либо результат walk-forward оптимизации.
  final Map<String, StrategyParams> _params = {
    'forts': StrategyParams.defaults,
    'crypto': StrategyParams.defaults,
    // Дневная стратегия раздела «Инвест» — тот же движок, другой таймфрейм.
    'stocks': StrategyParams.defaults,
  };

  /// Скринер для стратегии — собирается из её текущих параметров.
  /// У акций рабочий таймфрейм — дневки, и график обязан говорить правду.
  Screener _screenerFor(String strategyId) =>
      (_params[strategyId] ?? StrategyParams.defaults)
          .buildScreener(timeframeLabel: strategyId == 'stocks' ? '1D' : '1H');

  /// Скринер FORTS — базовый: его пороги показываются в параметрах.
  Screener get screener => _screenerFor('forts');

  /// Трендследящая стратегия — вторая система, не ветка первой.
  ///
  /// Периоды классические из правил Turtle; в сетку оптимизатора они
  /// сознательно не заводятся: подбирать их на нашей годовой истории значит
  /// подгонять две ручки под десяток дневных пробоев.
  static const _trendScreener = TrendScreener();

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

  /// Спецификации инструментов последнего пересчёта.
  ///
  /// Объём заявки считается по той же спецификации, по которой скринер принял
  /// решение: собирать её заново в момент отправки значит рисковать тем, что
  /// шаг цены или стоимость пункта разъедутся с теми, что были в расчёте.
  final Map<String, InstrumentSpec> _specBySymbol = {};

  /// Слушатель стадий расчёта: интерфейс показывает живой прогресс,
  /// а не зависшую заставку.
  @override
  void Function(String stage)? onProgress;

  void _stage(String stage) => onProgress?.call(stage);

  /// Загрузка состояния с диска — один раз на процесс, но **дожидаются её
  /// все**. Раньше здесь стоял флаг `_loaded`, который взводился до первого
  /// `await`: запуск читает журнал, стратегии и настройки параллельно
  /// (`Future.wait` в контроллере), и второй с третьим видели «уже загружено»,
  /// хотя с диска ещё ничего не пришло. Настройки собирались из дефолтов, а
  /// первая же запись состояния возвращала эти дефолты на диск — отсюда и
  /// «режим площадки всегда сбрасывается на тестнет».
  Future<void>? _loading;

  /// Вечный журнал сигналов: форвард-статистика стратегии на реальных свечах.
  @override
  final SignalLedger ledger = SignalLedger();

  /// Пропущенные идеи с причинами (ТЗ §12) — от новых к старым.
  final List<SkipRecord> skips = [];

  /// Исполнения планов по идеям (ТЗ §11.3, §17).
  ///
  /// Ключ — идея: одна идея исполняется один раз. Повторный вход по той же
  /// идее — это новая идея с новым решением, а не продолжение старой.
  final Map<String, Execution> executions = {};

  /// Риск-гейты перед публикацией идеи.
  final RiskEngine riskEngine = const RiskEngine(limits: _limits);

  /// Хранилище торговых ключей поверх Android Keystore.
  ///
  /// Подменяется в тестах: проверить, что отвергнутый ключ не удаляется,
  /// иначе можно только на устройстве.
  final SecureVault vault;

  /// Кто сейчас владеет состоянием на диске: интерфейс или фоновый контур.
  late final StateLock stateLock = StateLock(_store);

  /// Допуск стратегии к живым деньгам.
  static const _gate = LiveTradingGate();

  TradingState _trading = const TradingState();

  /// Брокеры по площадкам. Пересоздаются при смене режима, чтобы ключи
  /// тренировочного счёта физически не могли уйти на живой.
  final Map<BrokerId, Broker> _brokers = {};

  /// Кэш инструментов Т-Инвестиций — общий для всех режимов: соответствие
  /// «тикер → uid» от песочницы не зависит.
  late final StoredInstrumentCache _instruments = StoredInstrumentCache(_store);

  Broker _brokerFor(BrokerId id, TradingMode mode) => switch (id) {
        BrokerId.bybit => BybitBroker(
            mode: mode,
            apiKey: () => vault.apiKey(exchange: id.name, mode: mode.name),
            signer: (payload) =>
                vault.sign(exchange: id.name, mode: mode.name, payload: payload),
          ),
        // Токен Т-Инвестиций берётся по роли, а не по режиму площадки.
        //
        // Токен Invest API привязан к пользователю, а не к счёту: один
        // торговый токен видит все счета владельца. Поэтому их три —
        // песочница, чтение инвестиций и торговля по идеям, — и брокер
        // работает ровно тем, который соответствует роли.
        BrokerId.tinvest => TInvestBroker(
            mode: mode,
            role: tinvestRoleFor(mode),
            token: () => vault.apiKey(
              exchange: id.name,
              mode: tinvestRoleFor(mode).slot,
            ),
            instrumentCache: _instruments,
          )
          // Выбранный торговый счёт переживает пересоздание брокера: иначе
          // после смены режима заявка ушла бы с первого попавшегося счёта.
          ..tradingAccountId = _trading.tinvestAccountId
          // Счета, которые владелец разрешил читать. Токен видит все —
          // приложение работает только с этими.
          ..allowedAccountIds = _trading.allowedAccountIds,
      };

  /// Какая роль токена отвечает режиму площадки.
  ///
  /// Тренировочный режим — песочница. Живой — торговля по идеям: это
  /// единственный контур, которым распоряжается приложение. Токен на чтение
  /// инвестиций сюда не попадает никогда и живёт своей жизнью — им считается
  /// капитал и предлагается ребаланс, а сделки владелец делает сам.
  static TInvestRole tinvestRoleFor(TradingMode mode) =>
      mode == TradingMode.live ? TInvestRole.trade : TInvestRole.sandbox;

  /// Брокер площадки в её текущем режиме.
  @override
  Broker brokerOf(BrokerId id) =>
      _brokers[id] ??= _brokerFor(id, _trading.modeOf(id));

  @override
  TradingState get tradingState => _trading;

  /// Что подтвердила песочница каждой площадки. Читается с диска вместе с
  /// состоянием.
  final Map<BrokerId, SandboxProof> _sandbox = {};

  @override
  SandboxProof sandboxProofOf(BrokerId broker) =>
      _sandbox[broker] ?? const SandboxProof();

  /// Допуск по бумажной статистике — про стратегию, а не про площадку.
  ///
  /// Отпечаток песочницы сюда не подмешивается: он у каждой площадки свой, и
  /// общий вердикт «зарабатывает ли стратегия» от него не зависит. Механика
  /// проверяется там, где она применяется, — в [liveGateFor].
  @override
  GateVerdict get liveGate => _gate.evaluate(ledger);

  /// Допуск конкретной площадки к живым деньгам: и стратегия, и механика.
  ///
  /// Механика спрашивается только там, где её есть где проверить. У Bybit
  /// песочницы у владельца нет, и требование прогона закрыло бы площадку
  /// насовсем вместо того, чтобы что-то доказать.
  GateVerdict liveGateFor(BrokerId broker) => _gate.evaluate(
        ledger,
        proof: broker.hasSandbox ? sandboxProofOf(broker) : null,
      );

  /// Записать факт, подтверждённый песочницей площадки.
  ///
  /// Факт, а не намерение: сюда попадает только то, на что брокер ответил
  /// согласием. Иначе «проверено» означало бы «мы пытались».
  Future<void> _rememberSandbox(BrokerId broker, SandboxProof next) async {
    _sandbox[broker] = next;
    await _store.write('sandbox_proof', _sandboxJson());
  }

  Map<String, dynamic> _sandboxJson() =>
      {for (final e in _sandbox.entries) e.key.name: e.value.toJson()};

  @override
  Future<bool> hasBrokerKeys(BrokerId id) async {
    await _ensureLoaded();
    if (id == BrokerId.tinvest) {
      return vault.hasKeys(
        exchange: id.name,
        mode: tinvestRoleFor(_trading.modeOf(id)).slot,
        // У Т-Инвестиций авторизация одним токеном: секрета нет.
        needsSecret: false,
      );
    }
    return vault.hasKeys(exchange: id.name, mode: _trading.modeOf(id).name);
  }

  @override
  Future<Set<TradingMode>> brokerKeyModes(BrokerId id) async {
    final result = <TradingMode>{};
    for (final mode in TradingMode.values) {
      // У Т-Инвестиций слот именуется ролью токена, а не режимом площадки, и
      // секрета у него нет. Спрашивать по имени режима значило бы всегда
      // получать «ключей нет» и прятать площадку из капитала.
      final slot = id == BrokerId.tinvest ? tinvestRoleFor(mode).slot : mode.name;
      if (await vault.hasKeys(
        exchange: id.name,
        mode: slot,
        needsSecret: id != BrokerId.tinvest,
      )) {
        result.add(mode);
      }
    }
    return result;
  }

  @override
  Future<TradingMode?> readableMode(BrokerId id) async {
    await _ensureLoaded();
    final modes = await brokerKeyModes(id);
    if (modes.isEmpty) return null;
    final current = _trading.modeOf(id);
    return modes.contains(current) ? current : modes.first;
  }

  /// Брокер площадки в режиме, которым её можно читать.
  ///
  /// Возвращает null, если ключей нет ни для одного режима. Для чтения
  /// капитала это правильный компромисс: увидеть остатки живого счёта
  /// ключом live, когда переключатель стоит на testnet, безопасно — запросы
  /// только читающие. Для отправки заявок компромисса нет.
  Future<(Broker, TradingMode)?> _readableBroker(BrokerId id) async {
    // Т-Инвестиции читаются токеном «на чтение», если он заведён.
    //
    // Он для того и выдан: за ним стоит основной капитал, и именно его
    // остатки владелец хочет видеть. Токен песочницы показал бы счёт
    // песочницы, торговый — только счёт под идеи; ни тот ни другой не
    // отвечают на вопрос «сколько у меня денег». Раньше этот токен
    // использовался лишь для паспортов бумаг, и счета молчали.
    if (id == BrokerId.tinvest) {
      final invest = await _investReadBroker();
      if (invest != null) return (invest, TradingMode.live);
    }
    final mode = await readableMode(id);
    if (mode == null) return null;
    // Текущий режим отдаём тем же экземпляром — у него живёт выбранный
    // торговый счёт и кэш инструментов.
    if (mode == _trading.modeOf(id)) return (brokerOf(id), mode);
    return (_brokerFor(id, mode), mode);
  }

  /// Брокер Т-Инвестиций с токеном «на чтение». null — токена нет.
  ///
  /// Отдельный экземпляр, а не кэшированный: этот токен не участвует в
  /// торговле, и складывать его в общий кэш брокеров значило бы однажды
  /// отправить им заявку.
  TInvestBroker? _investRead;

  Future<TInvestBroker?> _investReadBroker() async {
    if (_investRead != null) return _investRead;
    final has = await vault.hasKeys(
      exchange: BrokerId.tinvest.name,
      mode: TInvestRole.invest.slot,
      needsSecret: false,
    );
    if (!has) return null;
    return _investRead = TInvestBroker(
      mode: TradingMode.live,
      role: TInvestRole.invest,
      token: () => vault.apiKey(
        exchange: BrokerId.tinvest.name,
        mode: TInvestRole.invest.slot,
      ),
      instrumentCache: _instruments,
    )..allowedAccountIds = _trading.allowedAccountIds;
  }

  @override
  Future<String> saveBrokerKeys({
    required BrokerId broker,
    required TradingMode mode,
    required String apiKey,
    required String apiSecret,
  }) async {
    await _ensureLoaded();
    if (!await vault.isAvailable) {
      throw const FeatureUnavailableException(
        'Защищённое хранилище недоступно на этом устройстве: ключи биржи '
        'сохранять некуда, а класть их в открытый файл нельзя.',
      );
    }
    // У Т-Инвестиций слот именуется ролью токена, а не режимом площадки:
    // токенов три, и складывать их в два слота значило бы перезаписывать
    // один другим.
    final slot = broker == BrokerId.tinvest
        ? tinvestRoleFor(mode).slot
        : mode.name;
    await vault.saveKeys(
      exchange: broker.name,
      mode: slot,
      apiKey: apiKey,
      apiSecret: apiSecret,
    );
    // Ключ проверяется сразу: молча сохранить нерабочий — значит узнать об
    // этом в момент отправки ордера.
    try {
      final probe = _brokerFor(broker, mode);
      final answer = await probe.checkAccess();
      if (mode == _trading.modeOf(broker)) _brokers[broker] = probe;
      await _rememberKeyCheck(broker, mode, ok: true, note: answer);
      return answer;
    } on BrokerException catch (e) {
      // Ключ остаётся в хранилище. Раньше он удалялся, и владелец, у которого
      // на секунду пропала сеть, был обязан заново набирать длинный секрет с
      // телефона — при том что с самим ключом всё было в порядке. Причина
      // отказа сохраняется и видна в настройках, пока проверка не пройдёт.
      final note =
          '${e.message}${await _crossCheckNote(broker, mode, rejection: e.message)}';
      await _rememberKeyCheck(broker, mode, ok: false, note: note);
      throw FeatureUnavailableException('${broker.title} не принял ключ: $note');
    }
  }

  /// Сохранить токен Т-Инвестиций под его ролью.
  ///
  /// Отдельный путь от [saveBrokerKeys] потому, что роль — не режим
  /// площадки. Токен на чтение инвестиций живёт вне переключателя
  /// «тренировка/бой»: им никогда ничего не отправляется, и связывать его с
  /// режимом торговли значило бы делать вид, что он тоже может торговать.
  Future<String> saveTinvestToken(TInvestRole role, String token) async {
    await _ensureLoaded();
    if (!await vault.isAvailable) {
      throw const FeatureUnavailableException(
        'Защищённое хранилище недоступно на этом устройстве: токен '
        'сохранять некуда, а класть его в открытый файл нельзя.',
      );
    }
    await vault.saveKeys(
      exchange: BrokerId.tinvest.name,
      mode: role.slot,
      apiKey: token,
      apiSecret: '',
    );
    final probe = TInvestBroker(
      mode: role == TInvestRole.trade ? TradingMode.live : TradingMode.testnet,
      role: role,
      token: () async => token,
      instrumentCache: _instruments,
    );
    try {
      final answer = await probe.checkAccess();
      _brokers.remove(BrokerId.tinvest);
      _investRead = null;
      return answer;
    } on BrokerException catch (e) {
      // Токен остаётся в хранилище: набирать его заново из-за секундного
      // отказа сети — наказание не по вине.
      throw FeatureUnavailableException('Т-Инвестиции не приняли токен: ${e.message}');
    }
  }

  Future<Set<TInvestRole>> tinvestTokenRoles() async {
    await _ensureLoaded();
    final result = <TInvestRole>{};
    for (final role in TInvestRole.values) {
      if (await vault.hasKeys(
        exchange: BrokerId.tinvest.name,
        mode: role.slot,
        needsSecret: false,
      )) {
        result.add(role);
      }
    }
    return result;
  }

  Future<void> setTinvestAccountAccess(String accountId, bool allowed) async {
    await _ensureLoaded();
    final next = {..._trading.allowedAccountIds};
    if (allowed) {
      next.add(accountId);
    } else {
      next.remove(accountId);
      // Счёт, у которого отобрали доступ, торговым быть перестаёт: иначе
      // отзыв доступа не отзывал бы главного.
      if (_trading.tinvestAccountId == accountId) {
        _trading = _trading.copyWith(tinvestAccountId: '');
      }
    }
    _trading = _trading.copyWith(allowedAccountIds: next);
    await _store.write('trading', _trading.toJson());
    _brokers.remove(BrokerId.tinvest);
    _investRead = null;
  }

  /// Проверка отвергнутого ключа Bybit на противоположной площадке.
  ///
  /// Самая частая причина отказа свежему ключу — он от другого сайта: у Bybit
  /// testnet и основная площадка не связаны, и ключи у них разные. Спорить об
  /// этом словами бессмысленно — приложение спрашивает вторую площадку тем же
  /// ключом и возвращает факт. Запрос только на чтение баланса; ордера с этой
  /// пробы уйти не могут.
  Future<String> _crossCheckNote(
    BrokerId broker,
    TradingMode mode, {
    required String rejection,
  }) async {
    if (broker != BrokerId.bybit) return '';
    // Проба осмысленна только когда ключ отверг сам Bybit. Если отказ местный
    // (нет сети, нечем подписать) — вторая площадка ответит тем же, и вывод
    // «ключ не принят нигде» был бы неправдой.
    if (!_authRejection(rejection)) return '';
    final opposite = mode == TradingMode.testnet ? TradingMode.live : TradingMode.testnet;
    final probe = BybitBroker(
      mode: opposite,
      // Ключ читается из слота исходного режима: проверяется именно та пара,
      // которую только что ввёл владелец, а не то, что лежит у другого режима.
      apiKey: () => vault.apiKey(exchange: broker.name, mode: mode.name),
      signer: (payload) =>
          vault.sign(exchange: broker.name, mode: mode.name, payload: payload),
    );
    try {
      await probe.checkAccess();
      return mode == TradingMode.testnet
          ? '. ПРИЧИНА НАЙДЕНА: этот ключ от основного bybit.com, а режим — '
              'testnet. Для testnet ключ создаётся на testnet.bybit.com'
          : '. ПРИЧИНА НАЙДЕНА: этот ключ от testnet.bybit.com, а режим — '
              'живой счёт. Для него ключ создаётся на bybit.com';
    } on BrokerException catch (e) {
      // Вторая площадка ключ тоже отвергла — значит, дело в самом ключе:
      // просрочен, отозван или ограничен по IP.
      if (_authRejection(e.message)) {
        return '. Проверено на обеих площадках Bybit: ключ не принят нигде — '
            'он просрочен, отозван либо ограничен по IP';
      }
      return '';
    } on Object {
      return '';
    } finally {
      probe.close();
    }
  }

  /// Похоже ли сообщение на отказ биржи в авторизации, а не на местный сбой.
  static bool _authRejection(String message) {
    const markers = ['401', '403', '10003', '10004', '10005', 'api key', 'invalid'];
    final lower = message.toLowerCase();
    return markers.any(lower.contains);
  }

  /// Итоги последней проверки ключей по площадкам и режимам.
  ///
  /// Ключ карты — `брокер/режим`: у песочницы и живого счёта ключи разные, и
  /// вердикт по одному ничего не говорит про другой.
  final Map<String, BrokerKeyCheck> _keyChecks = {};

  static String _keyCheckId(BrokerId broker, TradingMode mode) =>
      '${broker.name}/${mode.name}';

  Future<void> _rememberKeyCheck(
    BrokerId broker,
    TradingMode mode, {
    required bool ok,
    required String note,
  }) async {
    _keyChecks[_keyCheckId(broker, mode)] =
        BrokerKeyCheck(ok: ok, note: note, at: DateTime.now());
    await _persistState();
  }

  /// Проверка ключей площадки в её текущем режиме.
  @override
  BrokerKeyCheck? keyCheckOf(BrokerId broker) =>
      _keyChecks[_keyCheckId(broker, _trading.modeOf(broker))];

  @override
  Future<void> setTradingEnabled(bool enabled) async {
    await _ensureLoaded();
    _trading = _trading.copyWith(enabled: enabled);
    await _persistState();
  }

  @override
  Future<void> setTradingMode(BrokerId broker, TradingMode mode) async {
    await _ensureLoaded();
    // Живой режим Т-Инвестиций больше не запрещён на переключении, и это
    // исправление, а не послабление. Токен Invest API бывает выдан только на
    // боевой контур — песочницы у него нет вовсе. Приложение при этом
    // держало площадку в режиме «песочница», не находило по этой паре ключей
    // и показывало владельцу виртуальный счёт вместо настоящего: экран врал
    // о том, к чему подключён.
    //
    // Запрет никуда не делся — он переехал туда, где уходят деньги: в
    // `confirmSignal`. Наблюдать боевой счёт безопасно (запросы читающие),
    // отправлять в него заявку — нет, пока не подтвердится, что защитный
    // стоп встаёт вместе с позицией.
    //
    // Переключение Bybit на живой счёт открыто и при закрытом допуске: это
    // режим наблюдения — ключи проверяются, позиции видны, защита стопов
    // работает. Допуск сторожит не режим, а отправку ордеров: проверка стоит
    // в confirmSignal, в момент, когда деньги реально могут уйти. Владелец,
    // которому недоступен testnet, иначе не смог бы подключить биржу вовсе.
    _trading = _trading.withMode(broker, mode);
    _brokers.remove(broker);
    await _persistState();
  }

  @override
  Future<String> setKillSwitch(bool on) async {
    await _ensureLoaded();
    _trading = _trading.copyWith(killSwitch: on);
    await _persistState();
    if (!on) return 'Аварийная остановка снята';

    // Заявки снимаются у всех площадок, где есть ключи: «стоп, всё» означает
    // всё, а не только ту биржу, что открыта на экране.
    var cancelled = 0;
    final failures = <String>[];
    for (final id in BrokerId.values) {
      if (!await hasBrokerKeys(id)) continue;
      try {
        cancelled += await brokerOf(id).cancelAllOrders();
      } on BrokerException catch (e) {
        failures.add('${id.title}: ${e.message}');
      }
    }
    if (failures.isEmpty) return 'Остановлено: снято заявок — $cancelled';
    // Флаг всё равно поднят: новые ордера не уйдут. Молчать об этом нельзя —
    // заявки могли остаться.
    return 'Новые ордера запрещены, но снять удалось не всё: ${failures.join('; ')}';
  }

  // ── Фоновый контур ──────────────────────────────────────────────────────

  BackgroundMode _backgroundMode = BackgroundMode.persistent;
  bool _backgroundEnabled = false;

  @override
  BackgroundMode get backgroundMode => _backgroundMode;

  @override
  bool get backgroundEnabled => _backgroundEnabled;

  @override
  Future<void> setBackgroundMode(BackgroundMode mode) async {
    await _ensureLoaded();
    _backgroundMode = mode;
    await _persistState();
  }

  @override
  Future<void> setBackgroundEnabled(bool enabled) async {
    await _ensureLoaded();
    _backgroundEnabled = enabled;
    await _persistState();
  }

  @override
  Future<String> backgroundStateNote() async {
    if (!_backgroundEnabled) return 'выключен';
    final json = await _store.read('monitor');
    if (json == null) return 'включён, прогонов ещё не было';
    final state = MonitorState.fromJson(json);
    final at = state.lastRun;
    final when = at == null ? 'прогонов ещё не было' : 'последний прогон ${_timeLabel(at)}';
    final error = state.lastError;
    return error == null ? 'включён · $when' : '$when · сбой: $error';
  }

  @override
  Future<List<BrokerPosition>> brokerPositions() async {
    await _ensureLoaded();
    final result = <BrokerPosition>[];
    for (final id in BrokerId.values) {
      final readable = await _readableBroker(id);
      if (readable == null) continue;
      try {
        result.addAll(await readable.$1.positions());
      } on BrokerException {
        // Недоступная площадка не должна прятать позиции остальных.
      }
    }
    return result;
  }

  /// Счета Т-Инвестиций, видимые токеном.
  ///
  /// Нужны настройкам: владелец выбирает, с какого счёта приложение имеет
  /// право торговать. Остальные читаются для капитала и не торгуют.
  Future<List<TInvestAccount>> tinvestAccounts() async {
    final readable = await _readableBroker(BrokerId.tinvest);
    if (readable == null) return const [];
    final broker = readable.$1;
    if (broker is! TInvestBroker) return const [];
    try {
      return await broker.accounts(force: true);
    } on BrokerException {
      return const [];
    }
  }

  /// Какие счета считаются инвестиционными.
  ///
  /// Три условия, и каждое отсекает свою ошибку.
  ///
  /// Закрытый счёт отбрасывается: в выдаче он есть, но состава на нём нет.
  ///
  /// Неразрешённый — тоже, и пустой список разрешённых означает «доступ ещё
  /// не выдан», а не «можно всё». Токен видит все счета владельца, и
  /// умолчание «читаем то, что нашлось» было бы тихой выдачей доступа.
  ///
  /// Торговый счёт исключается отдельно, и это главное. С него уходят
  /// заявки по идеям; его состав — это открытые сделки, а не портфель.
  /// Смешать его с инвестиционным значило бы предложить ребаланс, который
  /// закрывает рабочие позиции.
  static List<TInvestAccount> investAccountsOf(
    List<TInvestAccount> accounts,
    TradingState trading,
  ) =>
      [
        for (final account in accounts)
          if (!account.closed &&
              trading.allows(account.id) &&
              account.id != trading.tinvestAccountId)
            account,
      ];

  /// Что лежит на инвестиционном счёте. null — читать нечем или нечего.
  ///
  /// Инвестиционный счёт — тот, который владелец разрешил читать и который
  /// **не** назначен торговым: с торгового идут заявки по идеям, и его
  /// состав к пакетам капитала отношения не имеет. Если разрешённых счетов
  /// несколько, берётся первый непустой: считать доли по сумме нескольких
  /// счетов нельзя — это разные портфели с разными целями.
  Future<InvestSnapshot?> investHoldings() async {
    await _ensureLoaded();
    final broker = await _investReadBroker();
    if (broker == null) return null;
    final List<TInvestAccount> accounts;
    try {
      accounts = await broker.accounts();
    } on BrokerException {
      return null;
    }
    for (final account in investAccountsOf(accounts, _trading)) {
      try {
        final holdings = await broker.holdings(account.id);
        if (holdings.isEmpty) continue;
        return InvestSnapshot(
          accountId: account.id,
          title: account.name,
          holdings: holdings,
        );
      } on BrokerException {
        // Один недоступный счёт не отменяет остальные.
      }
    }
    return null;
  }

  /// Назначает торговый счёт Т-Инвестиций.
  Future<void> setTinvestAccount(String? accountId) async {
    await _ensureLoaded();
    _trading = _trading.copyWith(tinvestAccountId: accountId);
    await _persistState();
    final broker = _brokers[BrokerId.tinvest];
    if (broker is TInvestBroker) broker.tradingAccountId = accountId;
  }

  // ── Пакеты капитала ────────────────────────────────────────────────────

  /// Историческая симуляция пакета по реальным сериям MOEX и Bybit.
  ///
  /// Это история, а не прогноз: считается, как заданный состав вёл себя
  /// раньше. Ряды берутся те же, что использует анализ, — индекс акций,
  /// индекс облигаций, фонд денежного рынка и биткоин.
  Future<PackageBacktest> simulatePackage(
    PackagePlan plan, {
    int years = 5,
  }) async {
    final from = DateTime.now().subtract(Duration(days: 365 * years));
    final series = <AssetClass, List<Candle>>{};

    for (final target in plan.targets) {
      final asset = target.assetClass;
      if (series.containsKey(asset)) continue;
      try {
        final candles = switch (asset) {
          AssetClass.stocks || AssetClass.dividendStocks =>
            await _iss.indexCandles(asset.benchmark, from: from),
          // Индекс полной доходности ОФЗ: он учитывает купоны, а «просто цена
          // облигации» занижает результат класса на всю купонную доходность.
          AssetClass.ofz || AssetClass.corpBonds || AssetClass.gold =>
            await _iss.indexCandles(asset.benchmark, from: from),
          // У флоатеров и валютных облигаций индекса полной доходности нет:
          // класс попадёт в «нет истории», и симуляция скажет об этом, а не
          // подставит похожий ряд.
          AssetClass.floaters || AssetClass.fxBonds => const <Candle>[],
          AssetClass.moneyMarket => await _iss.shareCandles('LQDT', from: from),
          AssetClass.crypto =>
            await _bybit.candles('BTCUSDT', timeframe: Timeframe.d1, limit: 1000),
        };
        if (candles.isNotEmpty) series[asset] = candles;
      } on Exception {
        // Ряд не пришёл — класс попадёт в «нет истории», и симуляция скажет
        // об этом, а не посчитает молча по остальным.
      }
    }

    return PackageSimulator.run(plan: plan, series: series);
  }

  /// Котировки прокси-инструментов пакета: цена штуки и размер лота.
  ///
  /// Без них пакет остаётся процентами, которые в терминал не выставляются.
  /// Инструмент, по которому цены нет, не подменяется похожим и не считается
  /// по нулю — строка разбора честно скажет, что количества нет.
  Future<Map<String, InstrumentQuote>> packageQuotes(PackagePlan plan) async {
    final symbols = {for (final t in plan.targets) t.assetClass.proxy};
    final quotes = <String, InstrumentQuote>{};

    final moex = {
      for (final t in plan.targets)
        if (!t.assetClass.onCrypto) t.assetClass.proxy,
    };
    if (moex.isNotEmpty) {
      try {
        for (final share in await _iss.sharesSnapshot()) {
          final symbol = share.spec.symbol.toUpperCase();
          if (!moex.contains(symbol)) continue;
          if (share.lastPrice <= 0) continue;
          quotes[symbol] = InstrumentQuote(
            symbol: symbol,
            price: Money.of(share.lastPrice, Currency.rub),
            lotSize: share.lotSize,
          );
        }
      } on Exception {
        // Снимок не пришёл — разбор скажет, что цен нет.
      }
    }

    if (symbols.contains('BTCUSDT')) {
      try {
        final tickers = await _bybit.tickers(symbols: const ['BTCUSDT']);
        if (tickers.isNotEmpty && tickers.first.lastPrice > 0) {
          quotes['BTCUSDT'] = InstrumentQuote(
            symbol: 'BTCUSDT',
            price: Money.of(tickers.first.lastPrice, Currency.usdt),
            venue: 'Bybit',
          );
        }
      } on Exception {
        // То же самое: молчание лучше выдуманной цены.
      }
    }

    return quotes;
  }

  /// Сколько штук каждого прокси-инструмента уже есть по книге.
  Future<Map<String, double>> packageHoldings(PackagePlan plan) async {
    final wanted = {for (final t in plan.targets) t.assetClass.proxy};
    final snapshot = (await _capital.state()).snapshot;
    final result = <String, double>{};
    for (final position in snapshot.positions) {
      final symbol = position.instrument.toUpperCase();
      if (!wanted.contains(symbol)) continue;
      // Один инструмент может лежать на нескольких счетах — складываем.
      result[symbol] = (result[symbol] ?? 0) + position.quantity.asDouble;
    }
    return result;
  }

  // ── Опционы ────────────────────────────────────────────────────────────

  /// Цепочка опционов на фьючерс базового актива.
  ///
  /// Данные публичные (ISS), поэтому раздел работает и без торговых ключей:
  /// анализировать конструкцию можно, не подключая брокера.
  Future<List<OptionContract>> optionChain(String assetCode) async =>
      (await optionChainProbe(assetCode)).$1;

  /// Цепочка вместе с протоколом запроса — для раздела и диагностики.
  ///
  /// Протокол показывается на экране, когда цепочка не пришла: «цепочки нет»
  /// не объясняет ничего, а «фильтр по активу, 1 запрос, 0 строк, колонки
  /// такие-то» объясняет всё.
  Future<(List<OptionContract>, OptionsChainResult)> optionChainProbe(
    String assetCode,
  ) async {
    final result = await _iss.optionsChainProbe(assetCode);
    return (
      [
      for (final row in result.rows)
        OptionContract(
          secId: row.secId,
          assetCode: row.assetCode,
          strike: row.strike,
          kind: OptionKind.parse(row.optionType),
          expiration: row.expiration,
          last: row.last,
          theoretical: row.theoretical,
          impliedVolatility: row.impliedVolatility,
          openInterest: row.openInterest,
          trades: row.trades,
          bid: row.bid,
          ask: row.ask,
          priceStep: row.priceStep,
          priceDecimals: row.priceDecimals,
        ),
      ],
      result,
    );
  }

  /// Сырой ответ биржи по адресу — для экрана диагностики.
  Future<IssRawProbe> probeIss(Uri url) => _iss.probeUrl(url);

  /// Цена ближайшего фьючерса на этот актив — база для расчёта конструкций.
  Future<double?> underlyingPrice(String assetCode) async {
    final snapshot = await _iss.fortsSnapshot();
    final code = assetCode.toUpperCase();
    for (final item in snapshot) {
      if (item.spec.symbol.toUpperCase().startsWith(code)) return item.lastPrice;
    }
    return null;
  }

  // ── Книга капитала ─────────────────────────────────────────────────────

  /// Синхронизация книги с площадками.
  ///
  /// Снимок брокера намеренно НЕ переписывает книгу: он создаёт наблюдение
  /// сверки. Иначе любой сбой площадки — пустой ответ, устаревший кэш,
  /// частичная выдача — молча стирал бы историю владельца. Здесь снимок
  /// делает три вещи: обновляет карточки счетов, кладёт последние цены для
  /// переоценки позиций и наполняет книгу выписками.
  @override
  Future<String> syncCapital() async {
    await _ensureLoaded();
    await _capital.load();

    final accounts = <Account>[..._capital.accounts];
    final notes = <String>[];
    var marked = 0;

    /// Подпись режима для карточки счёта: если читаем не тем режимом, что
    /// выбран переключателем, об этом должно быть написано, а не угадано.
    String modeNote(BrokerId id, TradingMode used) => used == _trading.modeOf(id)
        ? 'режим ${used.name}'
        : 'читаем ключом ${used.name}: для режима '
            '${_trading.modeOf(id).name} ключей нет';

    void upsert(Account account) {
      accounts
        ..removeWhere((a) => a.id == account.id)
        ..add(account);
    }

    // ── Т-Инвестиции: все счета токена ────────────────────────────────────
    //
    // Токен привязан к пользователю, а не к счёту: GetAccounts возвращает и
    // фьючерсный счёт, и тот, где лежит основной капитал. Раньше приложение
    // брало первый попавшийся — и половина капитала была невидима.
    final tinvest = await _readableBroker(BrokerId.tinvest);
    if (tinvest != null) {
      final (broker, tinvestMode) = tinvest;
      if (tinvestMode != _trading.modeOf(BrokerId.tinvest)) {
        notes.add(modeNote(BrokerId.tinvest, tinvestMode));
      }
      if (broker is TInvestBroker) {
        broker.tradingAccountId = _trading.tinvestAccountId;
        try {
          final list = await broker.accounts(force: true);
          notes.add('Т-Инвестиции — счетов ${list.length}');

          for (final account in list) {
            final id = 'tinvest:${account.id}';
            var imported = 0;
            var positionCount = 0;
            var failure = '';

            try {
              final positions = await broker.positions(accountId: account.id);
              positionCount = positions.length;
              for (final position in positions) {
                // Цена входа — не котировка, но это честная цена сделки, а не
                // выдуманное число: пока нет рыночной, позиция считается по ней.
                _capital.mark(
                  position.symbol,
                  Money.of(position.entryPrice, Currency.rub),
                );
                marked++;
              }
            } on BrokerException catch (e) {
              failure = e.message;
            }

            try {
              // Книга наполняется выпиской, а не портфелем: портфель говорит,
              // что есть сейчас, а книге нужно, как оно возникло. Повторный
              // импорт безопасен — дубли отбрасываются по номеру операции.
              final since = DateTime.now().subtract(const Duration(days: 370));
              final operations =
                  await broker.operations(from: since, accountId: account.id);
              final events = BrokerImport.fromOperations(
                accountId: id,
                venue: 'tinvest',
                operations: operations,
              );
              imported = await _capital.record(events);
              final unresolved = BrokerImport.unresolved(events);
              if (unresolved > 0) {
                notes.add('${account.title} — не разобрано операций: $unresolved');
              }
            } on BrokerException catch (e) {
              failure = failure.isEmpty ? e.message : failure;
            }

            upsert(Account(
              id: id,
              title: account.title,
              kind: account.isIis ? AccountKind.iis : AccountKind.broker,
              currency: Currency.rub,
              venue: 'Т-Инвестиции',
              // Права берутся у брокера, а не заявляются нами: read-only счёт
              // должен выглядеть read-only, даже если токен полный.
              permissions: ApiPermissions(read: true, trade: account.tradable),
              syncedAt: DateTime.now().toUtc(),
              reconcile: failure.isEmpty
                  ? ReconcileStatus.pending
                  : ReconcileStatus.stale,
              note: failure.isEmpty
                  ? [
                      if (account.tradable) 'торговый' else 'только чтение',
                      if (positionCount > 0) 'позиций $positionCount',
                      if (imported > 0) 'операций +$imported',
                    ].join(' · ')
                  : failure,
            ));
          }
        } on BrokerException catch (e) {
          notes.add('Т-Инвестиции — ${e.message}');
        }
      }
    } else {
      notes.add('Т-Инвестиции — ключей нет');
    }

    // ── Bybit ─────────────────────────────────────────────────────────────
    final bybit = await _readableBroker(BrokerId.bybit);
    if (bybit != null) {
      final (broker, bybitMode) = bybit;
      try {
        final positions = await broker.positions();
        for (final position in positions) {
          // Крипта котируется в USDT: класть её цену рублями значило бы
          // складывать разные валюты под видом одной.
          _capital.mark(position.symbol, Money.of(position.entryPrice, Currency.usdt));
          marked++;
        }

        var imported = 0;
        var equity = '';
        if (broker is BybitBroker) {
          try {
            final wallet = await broker.wallet();
            if (!wallet.isEmpty) {
              equity = 'капитал ${wallet.equity.toStringAsFixed(2)} USDT';
            }
          } on BrokerException {
            // Кошелёк не ответил — позиции уже показаны, а капитал по этому
            // счёту останется тем, что посчитан из книги.
          }
          try {
            // Журнал биржа хранит ограниченное время, поэтому импорт
            // инкрементальный: за 180 дней, а дубли книга отбросит сама.
            final since = DateTime.now().subtract(const Duration(days: 180));
            final log = await broker.transactionLog(from: since);
            final events = BrokerImport.fromOperations(
              accountId: 'bybit',
              venue: 'bybit',
              operations: log,
            );
            imported = await _capital.record(events);
            final unresolved = BrokerImport.unresolved(events);
            if (unresolved > 0) {
              notes.add('Bybit — не разобрано операций: $unresolved');
            }
          } on BrokerException {
            notes.add('Bybit — журнал операций не пришёл');
          }
        }

        upsert(Account(
          id: 'bybit',
          title: 'Bybit',
          kind: AccountKind.crypto,
          currency: Currency.usdt,
          venue: 'Bybit',
          permissions: const ApiPermissions(read: true, trade: true),
          syncedAt: DateTime.now().toUtc(),
          reconcile: ReconcileStatus.pending,
          note: [
            modeNote(BrokerId.bybit, bybitMode),
            if (positions.isNotEmpty) 'позиций ${positions.length}',
            if (imported > 0) 'операций +$imported',
            if (equity.isNotEmpty) equity,
          ].join(' · '),
        ));
        notes.add('Bybit — позиций ${positions.length}'
            '${imported > 0 ? ', импортировано операций $imported' : ''}');
      } on BrokerException catch (e) {
        upsert(Account(
          id: 'bybit',
          title: 'Bybit',
          kind: AccountKind.crypto,
          currency: Currency.usdt,
          venue: 'Bybit',
          permissions: const ApiPermissions(read: true, trade: true),
          reconcile: ReconcileStatus.stale,
          note: '${modeNote(BrokerId.bybit, bybitMode)} · ${e.message}',
        ));
        notes.add('Bybit — не ответил');
      }
    } else {
      notes.add('Bybit — ключей нет');
    }

    await _capital.saveAccounts(accounts);
    if (marked > 0) notes.add('переоценено позиций: $marked');
    return notes.isEmpty ? 'площадки не настроены' : notes.join(' · ');
  }

  @override
  Future<List<BrokerPosition>> unprotectedPositions() async {
    await _ensureLoaded();
    final result = <BrokerPosition>[];
    for (final id in BrokerId.values) {
      final readable = await _readableBroker(id);
      if (readable == null) continue;
      try {
        result.addAll(await readable.$1.unprotectedPositions());
      } on BrokerException {
        // Недоступная площадка не должна прятать голые позиции остальных.
      }
    }
    return result;
  }

  @override
  Future<bool> placeProtectiveStop({
    required String symbol,
    required double stopPrice,
    required bool long,
    required double quantity,
  }) async {
    for (final id in BrokerId.values) {
      if (!await hasBrokerKeys(id)) continue;
      // Площадка определяется по тому, у кого эта позиция открыта: гадать по
      // виду тикера нельзя, а лишний запрос к чужому брокеру безвреден.
      final mine = await brokerOf(id)
          .positions()
          .then((list) => list.any((p) => p.symbol == symbol))
          .catchError((_) => false);
      if (!mine) continue;
      final placed = await brokerOf(id).placeProtectiveStop(
        symbol: symbol,
        stopPrice: stopPrice,
        long: long,
        quantity: quantity,
      );
      // Стоп, вставший в песочнице, — это и есть та проверка механики,
      // которую раньше интерфейс объявлял, но не делал.
      if (placed && _trading.modeOf(id) == TradingMode.testnet) {
        await _rememberSandbox(
          id,
          sandboxProofOf(id).withStop(
            note: 'защита по $symbol встала на ${_num(stopPrice)}',
          ),
        );
      }
      return placed;
    }
    return false;
  }

  /// Активные заявки по всем подключённым площадкам.
  Future<List<({BrokerId broker, TradingMode mode, BrokerOrder order})>>
      brokerOrders() async {
    final result = <({BrokerId broker, TradingMode mode, BrokerOrder order})>[];
    for (final id in BrokerId.values) {
      if (!await hasBrokerKeys(id)) continue;
      try {
        for (final order in await brokerOf(id).orders()) {
          result.add((broker: id, mode: _trading.modeOf(id), order: order));
        }
      } on BrokerException {
        continue;
      }
    }
    return result;
  }

  DailyDigest? _cachedDigest;
  DateTime? _digestAt;
  DateTime? _optimizedAt;
  String? _optimizationNote;

  /// Ленивая загрузка сохранённого состояния — один раз на процесс.
  Future<void> _ensureLoaded() => _loading ??= _loadState();

  Future<void> _loadState() async {
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
        final trading = state['trading'] as Map<String, dynamic>?;
        if (trading != null) _trading = TradingState.fromJson(trading);
        final keyChecks = state['key_checks'] as Map<String, dynamic>?;
        if (keyChecks != null) {
          for (final entry in keyChecks.entries) {
            final check = BrokerKeyCheck.fromJson(entry.value as Map<String, dynamic>);
            if (check != null) _keyChecks[entry.key] = check;
          }
        }
        final sandbox = state['sandbox_proof'] as Map<String, dynamic>?;
        if (sandbox != null) {
          for (final entry in sandbox.entries) {
            final proof = entry.value;
            if (proof is! Map<String, dynamic>) continue;
            _sandbox[BrokerId.parse(entry.key)] = SandboxProof.fromJson(proof);
          }
        }
        final background = state['background'] as Map<String, dynamic>?;
        if (background != null) {
          _backgroundMode = BackgroundMode.parse(background['mode'] as String?);
          _backgroundEnabled = background['enabled'] as bool? ?? false;
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

        final edges = state['factor_edges'] as Map<String, dynamic>?;
        if (edges != null) {
          for (final entry in edges.entries) {
            _lastFactorEdges[entry.key] = [
              for (final e in entry.value as List<dynamic>)
                FactorEdge.fromJson(e as Map<String, dynamic>),
            ];
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

    final storedExecutions = await _store.read('executions');
    if (storedExecutions != null) {
      try {
        for (final item in storedExecutions['items'] as List? ?? const []) {
          final execution = Execution.fromJson(item as Map<String, dynamic>);
          executions[execution.ideaId] = execution;
        }
      } on Exception {
        // Битая запись исполнения не должна ломать запуск.
      }
    }

    final storedSkips = await _store.read('skips');
    if (storedSkips != null) {
      try {
        for (final item in storedSkips['items'] as List? ?? const []) {
          skips.add(SkipRecord.fromJson(item as Map<String, dynamic>));
        }
      } on Exception {
        // Битая запись пропуска не должна ломать запуск.
      }
    }

    final runs = await _store.read('digest_runs');
    if (runs != null) {
      try {
        for (final r in runs['items'] as List? ?? const []) {
          _digestRuns.add(DigestRun.fromJson(r as Map<String, dynamic>));
        }
      } on Exception {
        // Битый журнал прогонов не должен ломать запуск.
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

  /// Записать состояние можно только после того, как оно прочитано: иначе
  /// дефолты в памяти затирают сохранённое на диске. Путей записи много
  /// (бэктест, оптимизация, проверка ключа), и следить за каждым по отдельности
  /// — способ однажды пропустить один.
  Future<void> _persistState() async {
    await _ensureLoaded();
    await _store.write('state', {
      'risk': {'deposit': _risk.deposit, 'risk_percent': _risk.riskPercent},
      'strategy_enabled': _strategyEnabled,
      'channels': _channels,
      'notifications': _notifications,
      'params': {
        for (final entry in _params.entries) entry.key: entry.value.toJson(),
      },
      'trading': _trading.toJson(),
      'sandbox_proof': _sandboxJson(),
      'key_checks': {
        for (final entry in _keyChecks.entries) entry.key: entry.value.toJson(),
      },
      'background': {
        'mode': _backgroundMode.name,
        'enabled': _backgroundEnabled,
      },
      'optimized_at': _optimizedAt?.toIso8601String(),
      'optimization_note': _optimizationNote,
      'backtests': {
        for (final entry in _lastBacktests.entries)
          entry.key: entry.value.toJson(),
      },
      'factor_edges': {
        for (final entry in _lastFactorEdges.entries)
          entry.key: [for (final e in entry.value) e.toJson()],
      },
    });
  }

  // Подписи политики риска собираются из тех же чисел, которые применяет
  // [RiskEngine]: раньше это были декоративные строки, и написанное в
  // настройках ничего не значило.
  static const _limits = RiskLimits();

  RiskProfile _risk = RiskProfile(
    deposit: 1000000,
    riskPercent: 0.75,
    dailyLossLimit: '−${_limits.dailyLossLimitR.toStringAsFixed(1).replaceAll('.', ',')}R '
        '· торговля закрывается',
    maxConcurrentTrades: 'до ${_limits.maxConcurrent}',
    pauseRule: '${_limits.stopsBeforePause} стопа подряд — пауза '
        '${_limits.pauseAfterStops.inHours} ч',
  );
  Map<String, bool> _strategyEnabled = {
    'forts': true,
    'crypto': true,
    // Трендследование включено по умолчанию: это единственная система с
    // внешней доказательной базой. Выключить её — решение владельца.
    'trend': true,
  };
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

    try {
      return await _computeDigest();
    } on Object catch (e) {
      // Пересчёт не удался. Если на диске лежат прошлые идеи — показать их
      // с честной пометкой лучше, чем пустой экран «данные недоступны».
      final fallback = _cachedDigest;
      if (fallback == null) rethrow;
      _lastRefreshError = e;
      // Неудачный прогон — тоже прогон. Без этой записи журнал показывал бы
      // реже, чем считали, и владелец решил бы, что пересчёт не запускается,
      // тогда как он запускается и падает.
      await _recordRun(DigestRun(
        at: DateTime.now(),
        ideas: 0,
        candidates: 0,
        rejected: 0,
        note: 'не удалось: ${_shortReason(e)}',
        foreground: !inBackground,
      ));
      return fallback.withStaleNote(
        'Обновить не удалось (${_shortReason(e)}). Показаны идеи от '
        '${_timeLabel(_digestAt ?? DateTime.now())} — уровни могли устареть.',
      );
    }
  }

  /// Ошибка последнего неудачного пересчёта, когда показан старый кэш.
  Object? get lastRefreshError => _lastRefreshError;
  Object? _lastRefreshError;

  /// Считает ли этот экземпляр в фоновом изоляте.
  bool inBackground = false;

  /// Журнал пересчётов — от новых к старым.
  ///
  /// Владелец не мог отличить «пересчиталось и ничего не нашлось» от «не
  /// пересчитывалось». Настройка «раз в час» есть, а доказательства не было
  /// никакого. Журнал и есть доказательство: время, чем закончилось и кто
  /// считал — фон или экран.
  List<DigestRun> get digestRuns => List.unmodifiable(_digestRuns.reversed);
  final List<DigestRun> _digestRuns = [];

  static const _runsKept = 60;

  /// Сохранить пропуск. Журнал только дополняется — ТЗ §12: запись о
  /// решении неизменяема, иначе статистику по причинам можно подправить
  /// задним числом под то, во что приятнее верить.
  Future<void> recordSkip(SkipRecord record) async {
    await _ensureLoaded();
    skips.insert(0, record);
    await _store.write('skips', {
      'items': [for (final s in skips) s.toJson()],
    });
  }

  /// Сохранить состояние исполнения.
  ///
  /// Пишется на каждом шаге машины §11.3: без этого перезапуск приложения
  /// теряет знание о том, что позиция открыта, а защита ещё не встала.
  Future<void> saveExecution(Execution execution) async {
    await _ensureLoaded();
    executions[execution.ideaId] = execution;
    await _store.write('executions', {
      'items': [for (final e in executions.values) e.toJson()],
    });
  }

  Future<void> _recordRun(DigestRun run) async {
    _digestRuns.add(run);
    if (_digestRuns.length > _runsKept) {
      _digestRuns.removeRange(0, _digestRuns.length - _runsKept);
    }
    await _store.write('digest_runs', {
      'items': [for (final r in _digestRuns) r.toJson()],
    });
  }

  /// Был ли последний успешный расчёт неполным (один из источников молчал).
  bool get lastResultPartial => _lastPartial;
  bool _lastPartial = false;

  Future<DailyDigest> _computeDigest() async {
    final now0 = DateTime.now();
    _lastRefreshError = null;
    assert(now0.isBefore(now0.add(const Duration(days: 1))));
    lastRejections.clear();

    // Источники независимы: падение одного не должно уносить другой. Раньше
    // отказ MOEX убивал и крипто-идеи, а отказ Bybit — уже посчитанные идеи
    // FORTS. Теперь каждый источник огорожен, а чего не хватило — честно
    // написано в дайджесте.
    final unavailable = <String>[];

    _stage('Снимок срочного рынка MOEX…');
    var snapshots = <FortsSnapshot>[];
    if (_strategyEnabled['forts'] ?? true) {
      try {
        snapshots = await _iss.fortsSnapshot();
      } on Exception catch (e) {
        unavailable.add('MOEX ISS: ${_shortReason(e)}');
      }
    }
    final selected = _selectContracts(snapshots);
    final usdRub = _usdRub(selected);

    _stage('Режим рынка: индекс, валюта, биткоин…');
    final regime = await _regime(selected);
    final results = <ScreenerResult>[];

    // Часовики каждого инструмента прогона — ими же сверяется журнал сигналов.
    final hourlyBySymbol = <String, List<Candle>>{};

    // Спецификации и обороты по символам: без них риск-движок не посчитает
    // ни группу корреляции, ни лимит объёма от ликвидности. Спецификации
    // переживают пересчёт: по ним же считается объём заявки при подтверждении,
    // и это обязана быть та самая спецификация, по которой принято решение.
    final specBySymbol = _specBySymbol..clear();
    final turnoverBySymbol = <String, double>{};
    for (final snapshot in selected.values) {
      specBySymbol[snapshot.spec.symbol] = snapshot.spec;
      turnoverBySymbol[snapshot.spec.symbol] = snapshot.turnover;
    }

    if (_strategyEnabled['forts'] ?? true) {
      final fortsScreener = _screenerFor('forts');
      // Инструменты грузятся параллельно с ограничением: раньше 28 round-trip'ов
      // шли строго друг за другом, и даже на быстрой сети это десятки секунд.
      final contracts = selected.values.toList();
      var done = 0;
      final inputs = await _mapLimited<FortsSnapshot, InstrumentInput?>(
        contracts,
        (snapshot) async {
          final input = await _fortsInput(snapshot);
          done++;
          _stage('Анализ MOEX: $done из ${contracts.length}…');
          return input;
        },
      );
      for (final input in inputs) {
        if (input == null) continue;
        hourlyBySymbol[input.spec.symbol] = input.hourly;
        final result = fortsScreener.evaluate(
          input,
          regime,
          rejected: lastRejections,
          factorHistory: _lastFactorEdges['forts'],
        );
        if (result != null) results.add(result);
      }

      // Трендследование считается по тем же данным отдельным проходом.
      // Отдельным, а не веткой внутри скринера: у систем разная природа, и
      // усреднить их в один скоринг значит получить то, что не работает нигде.
      if (_strategyEnabled['trend'] ?? true) {
        for (final input in inputs) {
          if (input == null) continue;
          final result = _trendScreener.evaluate(
            input,
            reject: (reason) => lastRejections.add(
              RejectedCandidate(
                input.spec.symbol,
                'тренд: $reason',
                price: input.lastPrice,
              ),
            ),
          );
          if (result != null) results.add(result);
        }
      }
    }

    if (_strategyEnabled['crypto'] ?? true) {
      try {
        results.addAll(await _cryptoResults(
          regime,
          usdRub,
          hourlyBySymbol,
          specBySymbol,
          turnoverBySymbol,
        ));
      } on Exception catch (e) {
        unavailable.add('Bybit: ${_shortReason(e)}');
      }
    }

    // Ни один источник не ответил — отдавать пустой дайджест нечестно:
    // пусть вызывающий покажет прошлые идеи из кэша.
    if (unavailable.length >= _enabledSourceCount && results.isEmpty) {
      throw MarketDataException(
        'Ни один источник данных не ответил: ${unavailable.join('; ')}',
        kind: NetFailureKind.connection,
      );
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

    // Риск-гейты. Отказ здесь — не «фильтр качества», а прямой запрет
    // открывать позицию: экспозиция, дневной убыток, серия стопов, объём.
    final approved = <TradingSignal>[];
    for (final signal in signals) {
      final spec = specBySymbol[signal.symbol];
      if (spec == null) {
        approved.add(signal);
        continue;
      }
      final decision = riskEngine.check(
        spec: spec,
        signal: signal,
        ledger: ledger,
        risk: _risk,
        now: now,
        dailyTurnover: turnoverBySymbol[signal.symbol],
      );
      if (decision.allowed) {
        approved.add(signal);
      } else {
        lastRejections.add(RejectedCandidate(
          signal.symbol,
          'риск-лимит: ${decision.reason}',
          price: signal.entry,
        ));
      }
    }

    final digest = DailyDigest(
      title: 'Идеи на сегодня',
      subtitle: '${_dateLabel(now)} · расчёт на устройстве',
      deliveryBadges: const [],
      regime: _regimeQuotes(selected, regime),
      regimeNote: _regimeNote(regime),
      events: const [],
      signals: approved,
      signalsQuota: '${approved.length} из $maxIdeas',
      sourceNote: 'Идеи и уровни рассчитаны на этом устройстве в '
          '${_timeLabel(now)} по котировкам MOEX ISS и публичного Bybit. '
          'Это не демо-данные: график в карточке — те же свечи, по которым '
          'считался сигнал.'
          '${unavailable.isEmpty ? '' : ' Внимание: расчёт неполный — '
              '${unavailable.join('; ')}.'}',
      rejections: [
        for (final r in lastRejections) '${r.symbol} — ${r.reason}',
      ],
    );

    // Сначала фиксируем результат: посчитанный дайджест не должен пропадать
    // из-за проблемы в журнале, который к нему отношения не имеет.
    _cachedDigest = digest;
    _digestAt = now;
    _lastPartial = unavailable.isNotEmpty;
    await _store.write('digest', {
      'at': now.toIso8601String(),
      'partial': _lastPartial,
      'digest': digest.toJson(),
    });
    await _recordRun(DigestRun(
      at: now,
      ideas: approved.length,
      candidates: results.length,
      rejected: lastRejections.length,
      note: unavailable.isEmpty ? null : unavailable.join('; '),
      foreground: !inBackground,
    ));

    // Журнал: новые сигналы записываются, живые — проживаются вперёд по тем
    // же свечам, отбраковки получают цену для форвард-проверки.
    try {
      // В журнал попадают только те идеи, которые реально были бы взяты:
      // заблокированную риском сделку мы не открывали, и записывать её
      // бумажной означало бы мерить не ту стратегию.
      for (final signal in approved) {
        final spec = specBySymbol[signal.symbol];
        ledger.record(
          signal,
          now,
          costs: spec == null ? TradingCosts.none : defaultCostsFor(spec),
          fillMargin: spec?.tick ?? 0,
          breakevenAfterTp1: true,
        );
      }
      for (final r in lastRejections) {
        // Недоступность источника — не отбраковка. Вечный журнал отвечает на
        // «а что было бы, если бы взяли», а у «данные не пришли» такого
        // ответа нет: инструмент не отвергли, его не посмотрели.
        if (r.infrastructure) continue;
        final price = r.price;
        if (price != null) ledger.recordRejection(r.symbol, r.reason, price, now);
      }
      await _reconcileLedger(hourlyBySymbol);
    } on Object {
      // Журнал — вторичен по отношению к дайджесту: сверимся в следующий раз.
    }
    return digest;
  }

  /// Параллельный обход с ограничением одновременных запросов.
  ///
  /// Больше четырёх одновременных соединений мобильная сеть и биржевые API
  /// не любят, а меньше — не даёт выигрыша.
  static Future<List<R>> _mapLimited<T, R>(
    List<T> items,
    Future<R> Function(T item) body, {
    int concurrency = 4,
  }) async {
    final results = List<R?>.filled(items.length, null);
    var next = 0;
    Future<void> worker() async {
      while (true) {
        final index = next++;
        if (index >= items.length) return;
        results[index] = await body(items[index]);
      }
    }

    await Future.wait([
      for (var i = 0; i < concurrency && i < items.length; i++) worker(),
    ]);
    return [for (final r in results) r as R];
  }

  /// Сколько источников включено сейчас — чтобы понять, всё ли отвалилось.
  int get _enabledSourceCount {
    var count = 0;
    if (_strategyEnabled['forts'] ?? true) count++;
    if (_strategyEnabled['crypto'] ?? true) count++;
    return count == 0 ? 1 : count;
  }

  /// Короткая причина отказа для подписи дайджеста и строк журнала.
  ///
  /// Сырое `$e` сюда попадать не должно: владелец видел в журнале
  /// `нет данных: MarketDataException: HandshakeException...` и справедливо
  /// назвал это непонятной ошибкой. Класс исключения — деталь реализации,
  /// причина — то, что можно починить.
  static String _shortReason(Object e) {
    if (e is MarketDataException) {
      final advice = e.advice;
      return advice.isEmpty ? e.message : '${e.message}. $advice';
    }
    return 'источник не ответил';
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
      // Сопоставление и по тикеру, и по имени серии: у металлов и юаня
      // короткий код не совпадает с корнем (GOLD-9.26 торгуется как GDU6),
      // и сопоставление только по началу тикера молча теряло бы их.
      final series = snapshot.spec.name.toUpperCase();
      final root = fortsRoots.firstWhere(
        (r) => symbol.startsWith(r) || series.startsWith('$r-'),
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
    if (si == null || si.lastPrice <= 0) return _lastUsdRub;
    return _lastUsdRub = si.lastPrice / 1000;
  }

  Future<InstrumentInput?> _fortsInput(FortsSnapshot snapshot) async {
    try {
      final hourly = await _iss.candles(
        snapshot.spec.symbol,
        timeframe: Timeframe.h1,
        from: DateTime.now().subtract(const Duration(days: 14)),
        cache: true,
      );
      final daily = await _iss.candles(
        snapshot.spec.symbol,
        timeframe: Timeframe.d1,
        from: DateTime.now().subtract(const Duration(days: 90)),
        cache: true,
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
      lastRejections.add(RejectedCandidate.unreachable(
          snapshot.spec.symbol, 'свечи не пришли: ${_shortReason(e)}'));
      return null;
    }
  }

  Future<List<ScreenerResult>> _cryptoResults(
    MarketRegime regime,
    double usdRub,
    Map<String, List<Candle>> hourlyBySymbol,
    Map<String, InstrumentSpec> specBySymbol,
    Map<String, double> turnoverBySymbol,
  ) async {
    final results = <ScreenerResult>[];
    final cryptoScreener = _screenerFor('crypto');
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
          tickSize: ticker.lastPrice >= 1000 ? 1 : 0.01,
        );
        specBySymbol[ticker.symbol] = spec;
        // Оборот Bybit приходит в USDT — риск-движок считает в рублях.
        turnoverBySymbol[ticker.symbol] = ticker.turnover * usdRub;

        // Крипта считается СВОИМ скринером. Здесь стоял геттер `screener`,
        // который всегда возвращает конфигурацию FORTS: параметры, подобранные
        // walk-forward оптимизацией для крипты, сохранялись в `_params['crypto']`
        // и никогда не доходили до живой выдачи. Оптимизация крипты работала
        // вхолостую, а бэктест крипты мерил не то, чем торгуют.
        final result = cryptoScreener.evaluate(
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
          factorHistory: _lastFactorEdges['crypto'],
        );
        if (result != null) results.add(result);
      } on Exception catch (e) {
        lastRejections.add(RejectedCandidate.unreachable(
            ticker.symbol, 'данные не пришли: ${_shortReason(e)}'));
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
      exchange: await _exchangeRows(),
      gate: GateView(
        allowed: liveGate.allowed,
        reason: liveGate.reason,
        progress: liveGate.progress.clamp(0, 1).toDouble(),
      ),
      rejections: _rejectionRows(),
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
            signalId: t.signalId,
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
            signalId: t.signalId,
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
            id: 'trend',
            name: 'Трендследящая · канал Дончиана',
            description: 'Пробой канала на дневках, вход стоп-заявкой, выход по '
                'обратному пробою. Единственная система здесь с внешней '
                'доказательной базой: временной моментум документирован на 58 '
                'фьючерсах (JFE 2012) и 67 рынках с 1880 года (JPM 2017). На '
                'нашей годовой истории FORTS это не проверяется — дневных '
                'пробоев слишком мало, опора на публикации.',
            statsLabel: 'вселенная: ${fortsRoots.join(' · ')}\n'
                '${_trendScreener.label} · держит недели, сделок мало',
            enabled: _strategyEnabled['trend'] ?? true,
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
        factorEdges: [
          for (final edge in _lastFactorEdges['forts'] ?? _lastFactorEdges['crypto'] ?? const [])
            FactorEdgeRow(
              factor: edge.factor,
              edgeR: edge.edge,
              withCount: edge.withCount,
              withoutCount: edge.withoutCount,
              significant: edge.significant,
            ),
        ],
        riskLimits: _riskLimitsView(),
      );
  }

  RiskLimitsView _riskLimitsView() {
    final state = riskEngine.stateOf(ledger, DateTime.now());
    final until = state.pausedUntil;
    return RiskLimitsView(
      open: state.open,
      maxConcurrent: state.maxConcurrent,
      dayResultR: state.dayResultR,
      dailyLossLimitR: state.dailyLossLimitR,
      pauseNote: until == null
          ? 'пауза не активна'
          : 'пауза до ${_timeLabel(until)}',
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
          // Торговый доступ здесь намеренно не перечисляется: у него есть
          // собственная карточка «Торговый контур» с режимами, ключами и
          // проверками. Вторая витрина с кнопками «Подключить», которые
          // никуда не ведут, только запутывала.
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
          // Тумблера «Автопересчёт раз в час» здесь больше нет. Он не был
          // подключён ни к чему: пересчёт на переднем плане идёт независимо от
          // него, а настоящий часовой пересчёт при закрытом приложении даёт
          // только фоновый контур — отдельным переключателем. Настройка,
          // которая ничего не меняет, хуже отсутствующей: она обещает.
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
        trading: await _tradingView(),
        background: BackgroundView(
          modeLabel: _backgroundMode.label,
          persistent: _backgroundMode == BackgroundMode.persistent,
          enabled: _backgroundEnabled,
          stateNote: await backgroundStateNote(),
        ),
      );
  }

  @override
  Future<void> confirmSignal(String signalId) async {
    await _ensureLoaded();

    final signal = _signalById(signalId);
    _requireValidTradePlan(signal);
    final brokerId = BrokerId.forMarket(signal.market);
    if (brokerId == null) {
      throw FeatureUnavailableException(
        'Для рынка «${signal.market.name}» торгового адаптера пока нет.',
      );
    }
    if (!_trading.enabled) {
      throw const FeatureUnavailableException(
        'Торговля выключена: на биржу ничего не уходит. Включите её в '
        'настройках, в разделе «Торговый контур». На бумаге идею можно '
        'вести и без этого — кнопкой в карточке.',
      );
    }
    if (_trading.killSwitch) {
      throw const FeatureUnavailableException(
        'Активна аварийная остановка: новые заявки не отправляются.',
      );
    }
    final mode = _trading.modeOf(brokerId);
    // Допуск к живым деньгам проверяется здесь, а не при смене режима:
    // живой счёт можно подключить и наблюдать, но ордер на него уйдёт
    // только когда бумажная статистика заработала это право.
    // Т-Инвестиции на боевом счёте: защитный стоп там ставится отдельной
    // заявкой, и пока не подтверждено, что он встаёт вместе с позицией, вход
    // означал бы позицию без защиты. Раньше здесь стоял безусловный отказ —
    // снять его было нечем, потому что подтверждать было некому. Теперь
    // подтверждение приходит из песочницы: бумажная сделка по идее уходит
    // туда же и проверяет ровно эту связку.
    //
    // Проверка стоит здесь, а не на переключении режима: наблюдать за живым
    // счётом можно и без допуска, запрещена только отправка.
    final gate = liveGateFor(brokerId);
    if (mode == TradingMode.live && !gate.allowed) {
      throw FeatureUnavailableException(
        'Живой счёт в режиме наблюдения: ордера закрыты, пока допуск не '
        'открыт (${gate.reason}). Сделка может вестись на бумаге — '
        'кнопкой в карточке.',
      );
    }
    if (!await hasBrokerKeys(brokerId)) {
      throw FeatureUnavailableException(
        'Ключи ${brokerId.title} для режима «${mode.labelFor(brokerId)}» не заданы.',
      );
    }

    // Спецификация обязана быть: по ней считаются и риск, и объём. Раньше её
    // отсутствие означало тихий пропуск риск-гейтов — сделка уходила бы без
    // единой проверки. Теперь это отказ.
    final spec = await _specForSignal(signal);
    if (spec == null) {
      throw FeatureUnavailableException(
        'Не удалось получить параметры контракта ${signal.symbol}. '
        'Пересчитайте идеи и попробуйте снова.',
      );
    }

    // Риск-гейты применяются ещё раз перед отправкой: между показом идеи и
    // нажатием могло смениться всё — открыться позиция, выбраться дневной
    // лимит, начаться пауза после стопов.
    final decision = riskEngine.check(
      spec: spec,
      signal: signal,
      ledger: ledger,
      risk: _risk,
      now: DateTime.now(),
    );
    if (!decision.allowed) {
      throw FeatureUnavailableException('Риск-лимит: ${decision.reason}.');
    }

    // Подтверждение — последнее, что происходит перед отправкой: всё, что
    // могло запретить сделку, уже проверено, и человека не дёргают зря.
    final confirmed = await vault.confirm(
      title: '${signal.direction.isLong ? 'Покупка' : 'Продажа'} ${signal.symbol}',
      subtitle: '${brokerId.title} · ${mode.labelFor(brokerId)} · '
          'риск ${_num(_risk.riskPercent)}% депозита',
    );
    if (!confirmed) {
      throw const FeatureUnavailableException('Сделка не подтверждена.');
    }

    final quantity = _orderQuantity(signal, spec);
    if (quantity <= 0) {
      throw const FeatureUnavailableException(
        'Объём получился нулевым: риск на сделку меньше минимального лота.',
      );
    }

    final result = await brokerOf(brokerId).placeOrder(OrderRequest(
      symbol: signal.symbol,
      long: signal.direction.isLong,
      quantity: quantity,
      entry: signal.entry,
      stopEntry: signal.entryIsStop,
      stopLoss: signal.stopLoss,
      takeProfit: signal.takeProfits.isEmpty
          ? signal.entry
          : signal.takeProfits.first.price,
    ));
    if (!result.accepted) {
      throw FeatureUnavailableException(result.message);
    }
    if (mode == TradingMode.testnet) {
      await _rememberSandboxEntry(brokerId, signal.symbol, quantity);
    }
  }

  /// Что доказывает принятая песочницей заявка.
  ///
  /// Обе площадки ставят защиту вместе со входом и отменяют вход, если защита
  /// не встала: у Bybit стоп уходит полем той же заявки, у Т-Инвестиций —
  /// отдельным запросом, после отказа которого вход снимается. Поэтому
  /// принятая заявка означает и принятый стоп; выдумывать здесь нечего.
  Future<void> _rememberSandboxEntry(
    BrokerId broker,
    String symbol,
    double quantity,
  ) async {
    final note = '$symbol ${_num(quantity)} — заявка и защита приняты';
    await _rememberSandbox(
      broker,
      sandboxProofOf(broker).withOrder(note: note).withStop(note: note),
    );
  }

  /// Есть ли ключи песочницы у площадки — независимо от её текущего режима.
  Future<bool> _hasSandboxKeys(BrokerId id) => id == BrokerId.tinvest
      ? vault.hasKeys(
          exchange: id.name,
          mode: TInvestRole.sandbox.slot,
          needsSecret: false,
        )
      : vault.hasKeys(exchange: id.name, mode: TradingMode.testnet.name);

  // ── Бумажный журнал ─────────────────────────────────────────────────────

  @override
  String? paperNoteFor(String symbol) {
    for (final trade in ledger.openOrPending) {
      if (trade.symbol != symbol) continue;
      final since = 'с ${_timeLabel(trade.createdAt)}';
      return switch (trade.status) {
        PaperStatus.pending => 'ждёт входа по ${_num(trade.entry)} · $since',
        PaperStatus.open =>
          'в позиции${trade.unrealizedR == null ? '' : ' · ${_signedR(trade.unrealizedR!)}'} · $since',
        _ => since,
      };
    }
    return null;
  }

  @override
  Future<String> trackOnPaper(String signalId) async {
    await _ensureLoaded();
    return trackSignalOnPaper(_signalById(signalId));
  }

  @override
  Future<String> trackSignalOnPaper(TradingSignal signal) async {
    await _ensureLoaded();
    _requireValidTradePlan(signal);
    final existing = paperNoteFor(signal.symbol);
    if (existing != null) {
      return 'Идея уже ведётся на бумаге: $existing. Смотрите её на «Сделках».';
    }

    // Издержки и шаг цены берутся из спецификации ровно как при
    // автоматической записи: бумажная сделка обязана мериться теми же
    // правилами, иначе журнал перестанет сходиться с бэктестом.
    final spec = await _specForSignal(signal);
    ledger.record(
      signal,
      DateTime.now(),
      costs: spec == null ? TradingCosts.none : defaultCostsFor(spec),
      fillMargin: spec?.tick ?? 0,
      breakevenAfterTp1: true,
    );
    await _store.write('ledger', ledger.toJson());
    final mirrored = await _mirrorToSandbox(signal, spec);
    return 'Идея заведена на бумаге: лимитка ${_num(signal.entry)}, '
        'стоп ${_num(signal.stopLoss)}. Результат появится на «Сделках».'
        '$mirrored';
  }

  /// Провести бумажную идею ещё и через песочницу брокера.
  ///
  /// Бумага и песочница — не альтернативы, а два разных ответа об одной
  /// сделке. Журнал считает результат сам, вперёд по свечам, с издержками:
  /// он говорит, зарабатывает ли стратегия. Песочница — настоящий API
  /// брокера с ненастоящими деньгами: она говорит, примет ли он заявку,
  /// верно ли посчитан лот, встанет ли защита. На живой счёт нужны оба
  /// ответа, и получать их по очереди незачем — идея одна и та же.
  ///
  /// Результат песочницы намеренно не попадает в статистику журнала.
  /// Исполнение там нереалистично — свои фактические цены она берёт из
  /// упрощённой модели, — и подмешивать их в доходность значило бы мерить
  /// стратегию выдуманными сделками.
  ///
  /// Только Т-Инвестиции. У Bybit песочницы у владельца нет, и бумажная
  /// сделка по крипте остаётся целиком виртуальной — на устройстве. Это не
  /// упущение: отправлять её некуда, и делать вид, что механика крипты
  /// где-то проверена, нельзя.
  ///
  /// Ведение на бумаге от песочницы не зависит: недоступный брокер,
  /// отсутствующий токен, отказ по лоту — всё это возвращает строку-примечание
  /// и ничего не ломает. Обратное превратило бы бумагу в привилегию
  /// подключённого счёта.
  ///
  /// Общий тумблер торговли здесь не спрашивается, и это не небрежность:
  /// допуск к живым деньгам требует доказанной механики, а доказать её можно
  /// только прогоном. Требовать включённой торговли значило бы замкнуть
  /// круг — песочница не запускается без допуска, допуск не открывается без
  /// песочницы. Аварийная остановка при этом сильнее: она означает «никуда и
  /// ничего», и исключений у неё нет.
  Future<String> _mirrorToSandbox(
    TradingSignal signal,
    InstrumentSpec? spec,
  ) async {
    if (_trading.killSwitch) return '';
    final brokerId = BrokerId.forMarket(signal.market);
    if (brokerId == null || spec == null || !brokerId.hasSandbox) return '';
    if (!await _hasSandboxKeys(brokerId)) return '';
    final quantity = _orderQuantity(signal, spec);
    if (quantity <= 0) return '';
    try {
      final result = await _brokerFor(brokerId, TradingMode.testnet)
          .placeOrder(OrderRequest(
        symbol: signal.symbol,
        long: signal.direction.isLong,
        quantity: quantity,
        entry: signal.entry,
        stopEntry: signal.entryIsStop,
        stopLoss: signal.stopLoss,
        takeProfit: signal.takeProfits.isEmpty
            ? signal.entry
            : signal.takeProfits.first.price,
      ));
      if (!result.accepted) {
        return ' Песочница ${brokerId.title} заявку не приняла: '
            '${result.message}. На бумаге идея ведётся всё равно.';
      }
      await _rememberSandboxEntry(brokerId, signal.symbol, quantity);
      return ' Та же заявка ушла в песочницу ${brokerId.title} — '
          'механика исполнения проверена.';
    } on BrokerException catch (e) {
      return ' Песочница ${brokerId.title} недоступна (${e.message}).';
    }
  }

  /// Fail-closed защита legacy/on-device контура.
  ///
  /// Старый кэш мог пережить версию скринера, в которой структурный
  /// инвариант Entry/SL/TP ещё не проверялся. Исправлять цены задним числом
  /// нельзя: это уже другой торговый план. Поэтому paper и live получают
  /// одинаковый явный отказ и требуют пересчёта идеи.
  void _requireValidTradePlan(TradingSignal signal) {
    if (signal.tradePlanBlockers().isEmpty) return;
    throw const FeatureUnavailableException(
      'Торговый план повреждён: уровни Entry / SL / TP расположены '
      'некорректно. Пересчитайте идеи.',
    );
  }

  /// Идея из последней выдачи. Отсутствие — отказ, а не пустой результат.
  TradingSignal _signalById(String signalId) {
    for (final signal in _cachedDigest?.signals ?? const <TradingSignal>[]) {
      if (signal.id == signalId) return signal;
    }
    throw const FeatureUnavailableException(
      'Идея больше не в выдаче — пересчитайте идеи и попробуйте снова.',
    );
  }

  static String _signedR(double r) =>
      '${r >= 0 ? '+' : '−'}${r.abs().toStringAsFixed(2).replaceAll('.', ',')}R';

  // ── Раздел «Инвест»: среднесрочные идеи по акциям ───────────────────────

  /// Параметры дневного контура: лимитка живёт 5 торговых дней, позиция —
  /// до 60 (≈3 месяца). Тот же движок сделки, что в свинге, — другой шаг.
  static const investOrderTtlBars = 5;
  static const investMaxHoldBars = 60;

  /// Порог ликвидности вселенной: медианный дневной оборот ниже — бумага
  /// отбраковывается с причиной, а не пропадает молча.
  static const investMinTurnover = 50e6;

  /// Минимальный медианный дневной размах, % — граница между акцией и фондом.
  ///
  /// У фондов денежного рынка размах около 0,02%, у облигационных — десятые
  /// доли; у самой спокойной голубой фишки — больше процента. Порог 0,5%
  /// отсекает первых и не может задеть вторых.
  static const investMinRangePercent = 0.5;

  /// Эталон режима рынка акций.
  static const _investBenchmark = 'IMOEX';

  static const investTopIdeas = 5;

  /// Глубина дневной истории: ~3 года на бэктест и структуру.
  static const _investHistoryDays = 1100;

  InvestDigest? _investDigest;
  bool _investLoaded = false;

  /// Отдельный журнал бумажных сделок раздела — со свингом не смешивается:
  /// другой таймфрейм, другой горизонт, другая статистика.
  @override
  final SignalLedger investLedger = SignalLedger();

  /// Фундаментальные паспорта: Invest API, токен — тот же, что у брокера.
  late final TInvestFundamentals fundamentals = TInvestFundamentals(
    // Паспорта бумаг читаются инвестиционным токеном: это чтение, и
    // торговый токен для него не нужен.
    token: () => vault.apiKey(
      exchange: BrokerId.tinvest.name,
      mode: TInvestRole.invest.slot,
    ),
    store: _store,
  );

  Future<void> _ensureInvestLoaded() async {
    if (_investLoaded) return;
    _investLoaded = true;
    final storedLedger = await _store.read('invest_ledger');
    if (storedLedger != null) {
      try {
        final parsed = SignalLedger.fromJson(storedLedger);
        investLedger.trades.addAll(parsed.trades);
        investLedger.rejected.addAll(parsed.rejected);
      } on Exception {
        // Битый журнал не должен ломать раздел.
      }
    }
    final cached = await _store.read('invest');
    if (cached != null) {
      try {
        _investDigest = InvestDigest.fromJson(cached);
      } on Exception {
        _investDigest = null;
      }
    }
  }

  /// Пора ли ночной пересчёт: последний был не сегодня, и дневка уже закрыта
  /// (после 01:00 МСК ISS отдаёт финальные данные вчерашнего дня).
  bool investScanDue(DateTime mskNow) {
    final last = _investDigest?.at;
    if (last == null) return true;
    final sameDay = last.year == mskNow.year &&
        last.month == mskNow.month &&
        last.day == mskNow.day;
    if (sameDay) return false;
    return mskNow.hour >= 1;
  }

  @override
  Future<InvestDigest> fetchInvestDigest({bool force = false}) async {
    await _ensureLoaded();
    await _ensureInvestLoaded();
    final cached = _investDigest;
    if (!force && cached != null && !investScanDue(IssClient.mskNow())) {
      return cached;
    }
    return _computeInvestDigest();
  }

  Future<InvestDigest> _computeInvestDigest() async {
    _stage('Доска акций TQBR…');
    final shares = await _iss.sharesSnapshot();
    final rejected = <RejectedCandidate>[];

    final liquid = [
      for (final share in shares)
        // Неликвид поимённо не логируем — их сотни; итог виден числами
        // «доска / ликвидных» в шапке раздела.
        if (share.turnover >= investMinTurnover) share,
    ]..sort((a, b) => b.turnover.compareTo(a.turnover));

    final regime = await _investRegime();
    final regimeTrend = regime.forMarket(Market.moex);
    final screener = _screenerFor('stocks');
    final from = DateTime.now().subtract(const Duration(days: _investHistoryDays));

    final dailyBySymbol = <String, List<Candle>>{};
    final inputs = await _mapLimited<ShareSnapshot, InstrumentInput?>(
      liquid,
      (share) async {
        try {
          _stage('Анализ ${share.spec.symbol}…');
          final daily = await _iss.shareCandles(share.spec.symbol, from: from);
          if (daily.length < 260) {
            rejected.add(RejectedCandidate(share.spec.symbol,
                'мало дневной истории: ${daily.length} баров'));
            return null;
          }
          // Отсев фондов: денежный рынок и облигационные БПИФы стоят на доске
          // рядом с акциями, но у них нет волатильности — торговать по
          // структуре там нечего. Отличаем поведением, а не строкой
          // справочника: код типа инструмента биржа меняет, а физика — нет.
          final range = _medianRangePercent(daily);
          if (range < investMinRangePercent) {
            rejected.add(RejectedCandidate(
              share.spec.symbol,
              'не акция: дневной размах '
              '${range.toStringAsFixed(2).replaceAll('.', ',')}% — это фонд',
            ));
            return null;
          }
          dailyBySymbol[share.spec.symbol] = daily;
          return InstrumentInput(
            spec: share.spec,
            hourly: daily,
            daily: resampleWeeks(daily),
            lastPrice: share.lastPrice > 0 ? share.lastPrice : daily.last.close,
            changePercentLabel: _percentLabel(share.changePercent),
            changeUp: share.changePercent >= 0,
          );
        } on Exception catch (e) {
          rejected.add(RejectedCandidate.unreachable(
              share.spec.symbol, 'данные не пришли: ${_shortReason(e)}'));
          return null;
        }
      },
    );

    final tradable = [for (final i in inputs) ?i];
    final results = <ScreenerResult>[];
    final waiting = <ScreenerResult>[];
    for (final input in tradable) {
      final scratch = <RejectedCandidate>[];
      final result = screener.evaluate(
        input,
        regime,
        rejected: scratch,
        factorHistory: _lastFactorEdges['stocks'],
      );
      if (result == null) {
        // Вето по режиму — не то же самое, что «плохая бумага»: сетап есть,
        // мешает рынок. Такие кандидаты уходят в лист ожидания, чтобы работа
        // системы была видна и в нисходящем рынке.
        if (scratch.any((r) => r.reason.contains('против'))) {
          final neutral = screener.evaluate(input, MarketRegime.unknown);
          if (neutral != null && neutral.signal.direction.isLong) {
            waiting.add(neutral);
          }
        }
        rejected.addAll(scratch);
        continue;
      }
      if (!result.signal.direction.isLong) {
        // Решение владельца: шорт акций на 1–3 месяца не торгуем — платная
        // маржиналка съедает эдж, шорт-экспозиция есть через фьючерсы.
        rejected.add(RejectedCandidate(
            result.signal.symbol, 'шорт акций не торгуем: маржиналка съедает эдж'));
        continue;
      }
      results.add(result);
    }

    results.sort((a, b) => b.signal.score.compareTo(a.signal.score));
    waiting.sort((a, b) => b.signal.score.compareTo(a.signal.score));
    final top = results.take(investTopIdeas).toList();

    // Паспорта — только для показанных идей: гонять Invest API по всей доске
    // незачем, а отказ по одной бумаге не валит остальные.
    _stage('Фундаментальные паспорта…');
    final ideas = <InvestIdea>[];
    for (final result in top) {
      final passport = await fundamentals.passport(result.signal.symbol);
      ideas.add(InvestIdea(signal: result.signal, passport: passport));
    }

    final digest = InvestDigest(
      at: IssClient.mskNow(),
      universeSize: shares.length,
      liquidSize: liquid.length,
      tradableSize: tradable.length,
      ideas: ideas,
      watchlist: [
        for (final r in waiting.take(investTopIdeas)) InvestIdea(signal: r.signal),
      ],
      rejections: _groupRejections(rejected),
      regimeNote: _investRegimeNote(regimeTrend),
      regimeBlocksLongs: regimeTrend == StructureTrend.down,
      passportNote: ideas.any((i) => i.passport != null)
          ? ''
          : (fundamentals.lastError ?? ''),
    );
    _investDigest = digest;
    await _store.write('invest', digest.toJson());

    // Журнал: идеи записываются автоматически — форвард-статистика раздела
    // не должна зависеть от того, нажал ли владелец кнопку. Живые записи
    // проживаются вперёд по тем же дневкам.
    try {
      for (final idea in ideas) {
        final spec = _investSpec(liquid, idea.signal.symbol);
        investLedger.record(
          idea.signal,
          DateTime.now(),
          costs: spec == null ? TradingCosts.none : defaultCostsFor(spec),
          fillMargin: spec?.tick ?? 0,
          breakevenAfterTp1: true,
        );
      }
      // Свечи бумаг, живущих в журнале, но выпавших из топа, докачиваются —
      // иначе их сделки перестали бы проживаться.
      for (final trade in investLedger.openOrPending) {
        if (dailyBySymbol.containsKey(trade.symbol)) continue;
        try {
          dailyBySymbol[trade.symbol] =
              await _iss.shareCandles(trade.symbol, from: from);
        } on Exception {
          // Сверим в следующий раз.
        }
      }
      investLedger.reconcile(
        dailyBySymbol,
        orderTtlBars: investOrderTtlBars,
        maxHoldBars: investMaxHoldBars,
      );
      await _store.write('invest_ledger', investLedger.toJson());
    } on Object {
      // Журнал вторичен к дайджесту.
    }
    return digest;
  }

  /// Медианный дневной размах в процентах — по последним 60 барам.
  ///
  /// Медиана, а не среднее: одна дивидендная отсечка или день IPO не должны
  /// решать, акция это или фонд.
  static double _medianRangePercent(List<Candle> daily) {
    final tail = daily.length <= 60 ? daily : daily.sublist(daily.length - 60);
    final ranges = <double>[];
    for (final c in tail) {
      if (c.close <= 0) continue;
      ranges.add((c.high - c.low) / c.close * 100);
    }
    if (ranges.isEmpty) return 0;
    ranges.sort();
    return ranges[ranges.length ~/ 2];
  }

  /// Сводка отбраковки: по причине, со счётчиком и примерами.
  ///
  /// Причины с числами внутри («мало истории: 36 баров») схлопываются в одну
  /// группу — иначе каждая бумага давала бы свою строку, и сводка снова
  /// превратилась бы в свалку.
  static List<RejectionGroup> _groupRejections(List<RejectedCandidate> rejected) {
    final byReason = <String, List<String>>{};
    for (final r in rejected) {
      final key = r.reason.split(':').first.trim();
      byReason.putIfAbsent(key, () => []).add(r.symbol);
    }
    final groups = [
      for (final entry in byReason.entries)
        RejectionGroup(
          reason: entry.key,
          count: entry.value.length,
          examples: entry.value.take(6).toList(),
        ),
    ]..sort((a, b) => b.count.compareTo(a.count));
    return groups;
  }

  static String _investRegimeNote(StructureTrend trend) => switch (trend) {
        StructureTrend.up => 'Индекс МосБиржи: восходящая структура — '
            'лонг-идеи разрешены',
        StructureTrend.down => 'Индекс МосБиржи: нисходящая структура — '
            'лонг-идеи по акциям не выдаются',
        StructureTrend.flat => 'Индекс МосБиржи: структура во флэте — '
            'лонг-идеи разрешены, решает структура самой бумаги',
      };

  InstrumentSpec? _investSpec(List<ShareSnapshot> shares, String symbol) {
    for (final share in shares) {
      if (share.spec.symbol == symbol) return share.spec;
    }
    return null;
  }

  /// Режим рынка для акций: дневная структура индекса МосБиржи.
  ///
  /// Именно индекс, а не фьючерс на него: у фьючерса история рвётся на каждой
  /// экспирации, и «структура» считалась бы по склейке разных серий.
  Future<MarketRegime> _investRegime() async {
    try {
      final daily = await _iss.indexCandles(
        _investBenchmark,
        from: DateTime.now().subtract(const Duration(days: 400)),
      );
      if (daily.length < 20) return MarketRegime.unknown;
      return MarketRegime(
        indexTrend: analyzeStructure(daily).trend,
        currencyTrend: StructureTrend.flat,
        cryptoTrend: StructureTrend.flat,
      );
    } on Exception {
      // Режим неизвестен — идеи проходят без вето, и это видно в подписи.
      return MarketRegime.unknown;
    }
  }

  @override
  Future<BacktestResult> runInvestBacktest() async {
    await _ensureLoaded();
    await _ensureInvestLoaded();
    _stage('Доска акций TQBR…');
    final shares = await _iss.sharesSnapshot();
    final liquid = [
      for (final s in shares)
        if (s.turnover >= investMinTurnover) s,
    ]..sort((a, b) => b.turnover.compareTo(a.turnover));
    // Прогон по самым ликвидным: 40 бумаг × 3 года дневок — представительная
    // выборка, не превращающая телефон в печку.
    final subset = liquid.take(40).toList();
    final from = DateTime.now().subtract(const Duration(days: _investHistoryDays));

    final histories = <InstrumentHistory>[];
    for (final share in subset) {
      try {
        _stage('История ${share.spec.symbol}…');
        final daily = await _iss.shareCandles(share.spec.symbol, from: from);
        if (daily.length < 260) continue;
        histories.add(InstrumentHistory(
          spec: share.spec,
          hourly: daily,
          daily: resampleWeeks(daily),
        ));
      } on Exception {
        continue;
      }
    }
    if (histories.isEmpty) {
      throw const FeatureUnavailableException(
        'История акций не получена: биржа не отдала дневки. Попробуйте позже.',
      );
    }

    _stage('Режим рынка за всю историю…');
    final timeline = await _investRegimeTimeline();

    final summary = await _investBacktester(_screenerFor('stocks'))
        .run(histories, onProgress: onProgress, regime: timeline);
    final backtest = _lastBacktests['stocks'] = _formatBacktest(summary);
    _lastFactorEdges['stocks'] = computeFactorEdges(summary.trades);
    await _persistState();
    return backtest;
  }

  /// Последний бэктест дневной стратегии — для блока в разделе.
  @override
  BacktestResult? get investBacktest => _lastBacktests['stocks'];

  @override
  Future<String> optimizeInvestParameters() async {
    await _ensureLoaded();
    final shares = await _iss.sharesSnapshot();
    final liquid = [
      for (final s in shares)
        if (s.turnover >= investMinTurnover) s,
    ]..sort((a, b) => b.turnover.compareTo(a.turnover));
    final subset = liquid.take(25).toList();
    final from = DateTime.now().subtract(const Duration(days: _investHistoryDays));
    final histories = <InstrumentHistory>[];
    for (final share in subset) {
      try {
        final daily = await _iss.shareCandles(share.spec.symbol, from: from);
        if (daily.length < 260) continue;
        histories.add(InstrumentHistory(
          spec: share.spec,
          hourly: daily,
          daily: resampleWeeks(daily),
        ));
      } on Exception {
        continue;
      }
    }
    if (histories.isEmpty) {
      throw const FeatureUnavailableException('История акций не получена.');
    }
    final timeline = await _investRegimeTimeline();
    final outcome = await StrategyOptimizer(backtester: _investBacktester(null))
        .optimize(
      histories,
      onProgress: onProgress,
      screenerBuilder: (p) => p.buildScreener(timeframeLabel: '1D'),
      regime: timeline,
    );
    if (outcome.improved) {
      _params['stocks'] = outcome.params;
    }
    await _persistState();
    final pf = outcome.testProfitFactor;
    return outcome.improved
        ? 'акции: новые параметры, PF ${pf == null ? '∞' : pf.toStringAsFixed(1).replaceAll('.', ',')} '
            'на ${outcome.testTrades} сделках вне выборки'
        : 'акции: дефолт не обыгран — параметры не тронуты';
  }

  /// Бэктестер дневного контура: те же правила, шаг — день.
  Backtester _investBacktester(Screener? screener) => Backtester(
        screener: screener ?? _screenerFor('stocks'),
        windowBars: 260,
        orderTtlBars: investOrderTtlBars,
        maxHoldBars: investMaxHoldBars,
        cooldownBars: 3,
      );

  Future<RegimeTimeline> _investRegimeTimeline() async {
    try {
      final daily = await _iss.indexCandles(
        _investBenchmark,
        from: DateTime.now().subtract(const Duration(days: _investHistoryDays)),
      );
      return RegimeTimeline.build(index: daily);
    } on Exception {
      return RegimeTimeline.empty;
    }
  }

  /// Ночной прогон раздела для фонового контура: пересчёт, если пора, и
  /// уведомление о сильной идее. Пусто — пересчёт не требовался.
  @override
  Future<List<({String title, String body})>> investNightly() async {
    await _ensureLoaded();
    await _ensureInvestLoaded();
    if (!investScanDue(IssClient.mskNow())) return const [];
    final digest = await fetchInvestDigest(force: true);
    final top = digest.ideas.isEmpty ? null : digest.ideas.first.signal;
    if (top == null || top.score < 75) return const [];
    return [
      (
        title: 'Инвест: ${top.symbol} · Лонг · ${top.score}/100',
        body: 'Вход ${top.entry.toStringAsFixed(top.priceDecimals)} · '
            'SL ${top.stopLoss.toStringAsFixed(top.priceDecimals)} · '
            'горизонт до 3 месяцев. Разбор — на вкладке «Инвест».',
      ),
    ];
  }

  /// Спецификация инструмента идеи — нужна риск-движку и расчёту объёма.
  ///
  /// Сначала берётся та, по которой считался сигнал. Её может не быть после
  /// холодного старта, когда дайджест поднят из кэша, — тогда для срочного
  /// рынка она приходит из снимка (он кэширован пять минут и почти всегда
  /// уже в памяти), а для крипты собирается из самого сигнала.
  Future<InstrumentSpec?> _specForSignal(TradingSignal signal) async {
    final known = _specBySymbol[signal.symbol];
    if (known != null) return known;

    if (signal.market == Market.forts) {
      try {
        final snapshots = await _iss.fortsSnapshot();
        for (final s in snapshots) {
          if (s.spec.symbol == signal.symbol) return _specBySymbol[signal.symbol] = s.spec;
        }
      } on Exception {
        // Биржа не ответила — спецификации нет, и это честный отказ.
      }
      return null;
    }
    if (signal.market != Market.crypto) return null;
    return InstrumentSpec(
      id: signal.symbol.toLowerCase(),
      symbol: signal.symbol,
      name: signal.name,
      market: signal.market,
      priceDecimals: signal.priceDecimals,
      valuePerPoint: signal.unitMultiplier * _lastUsdRub,
      unitMultiplier: signal.unitMultiplier,
      unitDecimals: signal.unitDecimals,
      unitName: signal.unitName,
      unitRiskSuffix: signal.unitName,
      tickSize: signal.entry >= 1000 ? 1 : 0.01,
    );
  }

  /// Объём заявки в единицах биржи из риска на сделку.
  ///
  /// Единица расчёта у крипты — 0,01 монеты, у фьючерса — контракт; биржа в
  /// обоих случаях принимает объём в своих единицах, поэтому множитель берётся
  /// из спецификации, а не подставляется по рынку.
  double _orderQuantity(TradingSignal signal, InstrumentSpec spec) {
    final perUnit = spec.riskPerUnit(signal.unitRisk);
    if (perUnit <= 0) return 0;
    final units = _risk.riskRub / perUnit;
    final quantity = units * spec.unitMultiplier;
    return double.parse(quantity.toStringAsFixed(spec.unitDecimals + 1));
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

    _stage('Режим рынка за всю историю…');
    final timeline = await _regimeTimeline();

    final summary = await Backtester(screener: _screenerFor(strategyId))
        .run(histories, onProgress: onProgress, regime: timeline);
    final backtest = _lastBacktests[strategyId] = _formatBacktest(summary);
    _lastFactorEdges[strategyId] = computeFactorEdges(summary.trades);
    await _persistState();
    return backtest;
  }

  /// Эдж факторов последнего прогона — по стратегиям.
  final Map<String, List<FactorEdge>> _lastFactorEdges = {};

  /// Шкала исторического режима рынка: индекс, валюта, биткоин.
  ///
  /// Строится один раз на прогон и переиспользуется бэктестом и оптимизацией.
  /// Дневки якорей кэшируются, поэтому повторный вызов бирже не стоит ничего.
  Future<RegimeTimeline> _regimeTimeline() async {
    final from = DateTime.now().subtract(const Duration(days: 400));

    Future<List<Candle>> anchor(String? symbol) async {
      if (symbol == null) return const [];
      try {
        return await _iss.candles(symbol, timeframe: Timeframe.d1, from: from, cache: true);
      } on Exception {
        return const [];
      }
    }

    Map<String, FortsSnapshot> selected;
    try {
      selected = _selectContracts(await _iss.fortsSnapshot());
    } on Exception {
      selected = const {};
    }

    List<Candle> crypto;
    try {
      crypto = await _bybit.candles('BTCUSDT', timeframe: Timeframe.d1, limit: 400);
    } on Exception {
      crypto = const [];
    }

    return RegimeTimeline.build(
      index: await anchor((selected['MX'] ?? selected['RI'])?.spec.symbol),
      currency: await anchor(selected['SI']?.spec.symbol),
      crypto: crypto,
    );
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
        regime: await _regimeTimeline(),
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

  /// Ликвидное окно серии равно расстоянию между сериями.
  ///
  /// Фьючерс торгуется кварталами, но объём приходит в него только к концу
  /// жизни: прогон по всей истории контракта мерил бы стратегию на мёртвой
  /// доске, где спред шире стопа. Брать окно шире шага серий тоже нельзя —
  /// тогда соседние серии перекрываются, и один и тот же период рынка
  /// торгуется в прогоне дважды, раздувая число сделок.
  static Duration _liquidWindow(String secId) =>
      Duration(days: IssClient.isMonthlySeries(secId) ? 30 : 90);

  /// Сколько прошлых серий берём: год истории и там, и там.
  static int _seriesPerRoot(String secId) =>
      IssClient.isMonthlySeries(secId) ? 12 : 4;

  Future<List<InstrumentHistory>> _fortsHistories() async {
    _stage('MOEX: снимок рынка…');
    final snapshots = await _iss.fortsSnapshot();
    final selected = _selectContracts(snapshots);

    // Каждая серия — отдельная история в её ликвидном окне. Склейки цен нет,
    // поэтому ни одна сделка не проходит через ролловер: это единственный
    // способ получить год данных по FORTS и не выдумать при этом сделок,
    // которых не могло быть.
    final symbols = <(String, InstrumentSpec)>[];
    for (final snapshot in selected.values) {
      for (final series in IssClient.previousSeries(
        snapshot.spec.symbol,
        count: _seriesPerRoot(snapshot.spec.symbol),
      )) {
        symbols.add((series, snapshot.spec));
      }
    }

    var done = 0;
    final histories = await _mapLimited<(String, InstrumentSpec), InstrumentHistory?>(
      symbols,
      (entry) async {
        final (symbol, rootSpec) = entry;
        done++;
        _stage('История FORTS: $done из ${symbols.length}…');
        return _seriesHistory(symbol, rootSpec);
      },
    );
    return [for (final h in histories) ?h];
  }

  /// История одной серии в её ликвидном окне. null — данных не хватило.
  Future<InstrumentHistory?> _seriesHistory(String symbol, InstrumentSpec rootSpec) async {
    try {
      final hourly = await _iss.candles(
        symbol,
        timeframe: Timeframe.h1,
        from: DateTime.now().subtract(const Duration(days: 400)),
        cache: true,
      );
      if (hourly.length < 200) return null;

      // Ликвидное окно отсчитывается от последней свечи контракта: у
      // истёкшего это его экспирация, у текущего — сегодня.
      final cutoff = hourly.last.time.subtract(_liquidWindow(symbol));
      final liquidHourly = [for (final c in hourly) if (!c.time.isBefore(cutoff)) c];
      if (liquidHourly.length < 200) return null;

      // Дневки берутся за всю жизнь контракта, а не только за ликвидное окно:
      // это контекст и ATR, торговать по ним прогон не будет, а сорока
      // дневных свечей в месячном окне просто нет.
      final daily = await _iss.candles(
        symbol,
        timeframe: Timeframe.d1,
        from: DateTime.now().subtract(const Duration(days: 400)),
        cache: true,
      );
      if (daily.length < 40) return null;

      return InstrumentHistory(
        // Спецификация берётся от текущей серии корня: размер контракта и шаг
        // цены внутри корня не меняются, а истёкшей серии в снимке уже нет.
        spec: InstrumentSpec(
          id: symbol.toLowerCase(),
          symbol: symbol,
          name: rootSpec.name,
          market: rootSpec.market,
          priceDecimals: rootSpec.priceDecimals,
          valuePerPoint: rootSpec.valuePerPoint,
          unitMultiplier: rootSpec.unitMultiplier,
          unitDecimals: rootSpec.unitDecimals,
          unitName: rootSpec.unitName,
          unitRiskSuffix: rootSpec.unitRiskSuffix,
          tickSize: rootSpec.tickSize,
        ),
        hourly: liquidHourly,
        daily: daily,
      );
    } on Exception {
      // Серии, которой не было, ISS отдаст пустоту — она просто не участвует.
      return null;
    }
  }

  Future<List<InstrumentHistory>> _cryptoHistories() async {
    _stage('Bybit: тикеры…');
    final tickers = await _bybit.tickers(symbols: cryptoSymbols);
    final usdRub = await _usdRubFromMarket();
    final from = DateTime.now().subtract(const Duration(days: 365));
    final histories = <InstrumentHistory>[];
    for (final ticker in tickers) {
      _stage('История ${ticker.symbol}…');
      try {
        final hourly =
            await _bybit.candles(ticker.symbol, timeframe: Timeframe.h1, from: from);
        final daily = await _bybit.candles(
          ticker.symbol,
          timeframe: Timeframe.d1,
          from: DateTime.now().subtract(const Duration(days: 400)),
        );
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
              tickSize: ticker.lastPrice >= 1000 ? 1 : 0.01,
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

  /// Курс доллара с рынка — из фьючерса Si, а не из константы.
  ///
  /// Снимок кэширован, поэтому лишнего обхода биржи здесь нет. Если ISS
  /// недоступен, возвращается последний известный курс: подставлять «90»
  /// в 2026 году значит врать в рублёвом риске на десятки процентов.
  Future<double> _usdRubFromMarket() async {
    try {
      final rate = _usdRub(_selectContracts(await _iss.fortsSnapshot()));
      _lastUsdRub = rate;
      return rate;
    } on Exception {
      return _lastUsdRub;
    }
  }

  double _lastUsdRub = 90;

  BacktestResult _formatBacktest(BacktestSummary summary) {
    final pf = summary.profitFactor;
    return BacktestResult(
      info: '${summary.days} дн · ${summary.instruments} серий · на устройстве · '
          'издержки −${summary.averageCostR.abs().toStringAsFixed(2).replaceAll('.', ',')}R'
          '/сделку · режим рынка исторический',
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

  /// Позиции и заявки со всех подключённых площадок.
  ///
  /// Отдельно от бумажного журнала и с пометкой площадки: перепутать «что я
  /// себе записал» и «что стоит на бирже» нельзя ни на секунду.
  Future<List<ExchangeRow>> _exchangeRows() async {
    final rows = <ExchangeRow>[];
    for (final id in BrokerId.values) {
      if (!await hasBrokerKeys(id)) continue;
      final venue = '${id.title} · ${_trading.modeOf(id).labelFor(id).split(' — ').first}';
      try {
        for (final p in await brokerOf(id).positions()) {
          rows.add(ExchangeRow(
            symbol: p.symbol,
            venue: venue,
            detail: '${p.long ? 'лонг' : 'шорт'} ${_num(p.quantity)}'
                '${p.entryPrice > 0 ? ' от ${_num(p.entryPrice)}' : ''}'
                '${p.unrealizedPnl == 0 ? '' : ' · ${_num(p.unrealizedPnl)}'}',
            position: true,
            tone: p.unrealizedPnl >= 0 ? Tone.positive : Tone.negative,
          ));
        }
        for (final o in await brokerOf(id).orders()) {
          rows.add(ExchangeRow(
            symbol: o.symbol,
            venue: venue,
            detail: 'заявка ${o.long ? 'на покупку' : 'на продажу'} '
                '${_num(o.quantity)} по ${_num(o.price)} · ${o.status}',
            position: false,
          ));
        }
      } on BrokerException catch (e) {
        rows.add(ExchangeRow(
          symbol: id.title,
          venue: venue,
          detail: 'не удалось получить состояние: ${e.message}',
          position: false,
          tone: Tone.negative,
        ));
      }
    }
    return rows;
  }

  /// Последние отбраковки с форвард-проверкой «а что было бы, если бы взяли».
  ///
  /// Возраст режется здесь тоже, а не только при записи: журнал может месяц
  /// пролежать без единой новой отбраковки, и тогда чистка при записи не
  /// случится ни разу — а на экране всё это время висит позапрошлый месяц.
  List<RejectionRow> _rejectionRows() {
    ledger.pruneRejections(DateTime.now());
    final recent = ledger.rejected.reversed.take(20);
    return [
      for (final r in recent)
        RejectionRow(
          symbol: r.symbol,
          reason: r.reason,
          moveLabel: r.movePercent24h == null
              ? '—'
              : '${r.movePercent24h! >= 0 ? '+' : '−'}'
                  '${r.movePercent24h!.abs().toStringAsFixed(1).replaceAll('.', ',')}%',
          movePositive: (r.movePercent24h ?? 0) >= 0,
        ),
    ];
  }

  /// Снимок торгового контура для настроек.
  Future<TradingView> _tradingView() async {
    final gate = liveGate;
    final brokers = <BrokerView>[];
    for (final id in BrokerId.values) {
      final mode = _trading.modeOf(id);
      // Живой режим закрыт, пока механика исполнения не проверена в
      // песочнице. Раньше здесь стояла строка «защитный стоп ещё не проверен
      // на песочнице» — захардкоженная: она показывалась всегда, независимо
      // от того, был ли прогон. Приложение объявляло проверку, которой не
      // делало, и снять этот запрет было нечем.
      //
      // Теперь причина берётся из отпечатка того, что песочница ответила на
      // самом деле, и исчезает сама, когда прогон состоялся. У Bybit
      // песочницы нет — там причины и не возникает.
      final blocked = id.hasSandbox ? sandboxProofOf(id).missing : '';
      final check = keyCheckOf(id);
      final hasKeys = await hasBrokerKeys(id);
      brokers.add(BrokerView(
        id: id.name,
        title: id.title,
        modeLabel: mode.labelFor(id),
        live: mode == TradingMode.live,
        hasKeys: hasKeys,
        liveAllowed: blocked.isEmpty,
        liveBlockedReason: blocked,
        keyNote: check == null ? '' : '${_timeLabel(check.at)} · ${check.note}',
        keysAccepted: hasKeys && (check?.ok ?? false),
        modeNote: mode == TradingMode.live && !gate.allowed
            ? 'Наблюдение: счёт, позиции и защита стопов работают, ордера '
                'закрыты до допуска (${gate.reason})'
            : '',
      ));
    }

    return TradingView(
      brokers: brokers,
      enabled: _trading.enabled,
      killSwitch: _trading.killSwitch,
      gateAllowed: gate.allowed,
      gateReason: gate.reason,
      gateProgress: gate.progress.clamp(0, 1).toDouble(),
      vaultAvailable: await vault.isAvailable,
      confirmMethod: await vault.confirmMethod,
    );
  }

  // ── Диагностика торгового контура ───────────────────────────────────────

  @override
  Stream<TradingCheck> diagnoseTrading() async* {
    await _ensureLoaded();
    yield* diagnoseTradingWith(this);
  }

  @override
  Future<bool> get vaultAvailable => vault.isAvailable;

  @override
  Future<ConfirmMethod> get confirmMethod => vault.confirmMethod;

  @override
  Future<String?> get confirmMethodDetails => vault.confirmMethodDetails;

  @override
  bool get tradingEnabled => _trading.enabled;

  @override
  bool get killSwitch => _trading.killSwitch;

  @override
  List<BrokerId> get probedBrokers => BrokerId.values;

  @override
  String modeLabelOf(BrokerId broker) => _trading.modeOf(broker).labelFor(broker);



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

/// Одна запись журнала пересчётов.
///
/// Существует ради одного вопроса владельца: «правда ли пересчёт идёт раз в
/// час». Отвечать на него настройкой нельзя — настройка это обещание, а не
/// факт. Отвечает список: когда считали, что нашли и кто считал.
class DigestRun {
  const DigestRun({
    required this.at,
    required this.ideas,
    required this.candidates,
    required this.rejected,
    required this.foreground,
    this.note,
  });

  final DateTime at;

  /// Сколько идей вышло в дайджест.
  final int ideas;

  /// Сколько кандидатов дошло до отбора.
  final int candidates;

  /// Сколько отбраковано с причиной.
  final int rejected;

  /// Считал экран (true) или фоновый контур (false).
  final bool foreground;

  /// Причина неудачи либо недоступный источник. null — прогон прошёл целиком.
  final String? note;

  bool get failed => note != null && note!.startsWith('не удалось');

  Map<String, dynamic> toJson() => {
        'at': at.toIso8601String(),
        'ideas': ideas,
        'candidates': candidates,
        'rejected': rejected,
        'fg': foreground,
        if (note != null) 'note': note,
      };

  static DigestRun fromJson(Map<String, dynamic> json) => DigestRun(
        at: DateTime.tryParse(json['at'] as String? ?? '') ?? DateTime.now(),
        ideas: (json['ideas'] as num?)?.toInt() ?? 0,
        candidates: (json['candidates'] as num?)?.toInt() ?? 0,
        rejected: (json['rejected'] as num?)?.toInt() ?? 0,
        foreground: json['fg'] as bool? ?? true,
        note: json['note'] as String?,
      );
}
