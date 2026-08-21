import 'dart:async';

import 'package:flutter/foundation.dart';

import '../core/app_mode.dart';
import '../data/api/api_client.dart';
import '../data/api/api_config.dart';
import '../data/api/device_enrollment.dart';
import '../data/api/engine_contract.dart';
import '../data/api/engine_client.dart';
import '../data/api/engine_runtime.dart';
import '../data/broker/tinvest_broker.dart';
import '../data/local_analysis_repository.dart';
import '../data/local_store.dart';
import '../data/market/idea_chart_source.dart';
import '../data/market/iss_client.dart';
import '../data/market/net_failure.dart';
import '../data/mock/demo_ideas.dart';
import '../data/mock/demo_repository.dart';
import '../data/native_bridge.dart';
import '../data/ledger/capital_desk.dart';
import '../data/repository.dart';
import '../domain/ledger/account.dart';
import '../domain/ledger/ledger_event.dart';
import '../domain/ledger/money.dart';
import '../domain/ledger/signal_ledger.dart';
import '../domain/options/structure_builder.dart';
import '../domain/portfolio/allocation.dart';
import '../domain/portfolio/package.dart';
import '../domain/research/hypothesis.dart';
import '../domain/portfolio/package_backtest.dart';
import '../domain/portfolio/package_plan.dart';
import '../domain/portfolio/rebalance.dart';
import '../domain/options/structures.dart';
import '../domain/risk/portfolio_impact.dart';
import '../domain/risk/risk_engine.dart';
import 'navigation.dart';
import '../domain/broker/broker.dart';
import '../domain/broker/tinvest_role.dart';
import '../domain/broker/trading_diagnostics.dart';
import '../domain/enums.dart';
import '../domain/idea/execution.dart';
import '../domain/idea/final_check.dart';
import '../domain/idea/idea.dart';
import '../domain/idea/idea_state.dart';
import '../domain/idea/journal_metrics.dart';
import '../domain/idea/paper_position.dart';
import '../domain/idea/risk_center.dart';
import '../domain/idea/skip_record.dart';
import '../domain/invest/invest_models.dart';
import '../domain/models/digest.dart';
import '../domain/models/portfolio.dart';
import '../domain/models/settings.dart';
import '../domain/models/signal.dart';
import '../domain/models/strategy.dart';
import '../data/state_lock.dart';
import '../monitor/background_cycle.dart';
import '../monitor/background_mode.dart';

/// Вкладки нижней навигации.
enum AppTab { ideas, invest, trades, strategies, settings }

/// Состояние площадки для экрана счетов.
///
/// Площадка перечисляется всегда, даже если ключей нет: строка с причиной
/// честнее пустоты. Пустота на месте Bybit читается как «этой биржи в
/// приложении нет», и владелец справедливо заключает, что приложение врёт.
class VenueStatus {
  const VenueStatus({
    required this.id,
    required this.mode,
    required this.keyModes,
    required this.readable,
    required this.check,
  });

  final BrokerId id;

  /// Режим, выбранный переключателем.
  final TradingMode mode;

  /// Режимы, для которых ключи реально лежат в хранилище.
  final Set<TradingMode> keyModes;

  /// Режим, которым площадку можно читать. null — читать нечем.
  final TradingMode? readable;

  /// Итог последней проверки ключа биржей.
  final BrokerKeyCheck? check;

  String get title => id.title;

  bool get hasKeys => keyModes.isNotEmpty;

  /// Совпадает ли режим переключателя с тем, чем читаем.
  bool get modeMatches => readable == mode;

  /// Почему площадка молчит. null — всё в порядке.
  String? get problem {
    if (!hasKeys) return 'ключи не заданы: «Контроль → Интеграции»';
    if (!modeMatches) {
      return 'переключатель стоит на ${mode.name}, а ключи есть только для '
          '${keyModes.map((m) => m.name).join(' и ')} — читаем ими, '
          'но заявки с этого режима не уйдут';
    }
    final check = this.check;
    if (check != null && !check.ok) return 'биржа не приняла ключ: ${check.note}';
    return null;
  }
}

/// Состояние приложения: что показываем и что сейчас делает пользователь.
///
/// Никакой торговой логики здесь нет — только загрузка данных, навигация и
/// оптимистичные переключатели. Решения принимает сервер (ТЗ §2).
class AppController extends ChangeNotifier {
  AppController(
    this._repository, {
    NativeBridge bridge = const NativeBridge(),
    LocalStore? prefs,
    EngineClient? engine,
    IdeaChartSource? chartSource,
    bool thinMode = AppMode.thin,
  })  : _bridge = bridge,
        _engine = engine ?? EngineClient(),
        _demoChartSource = chartSource,
        _thinMode = thinMode,
        _prefs = prefs ?? LocalStore() {
    // Часовой пульс: пока приложение живо, идеи не старше часа. Проверка
    // раз в минуту, пересчёт — когда дайджест реально устарел.
    _autoRefreshTimer = Timer.periodic(const Duration(minutes: 1), (_) {
      // Отметка владения состоянием: пока приложение на переднем плане, оно
      // единственный писатель, и фоновый контур в это время не считает.
      _lock?.heartbeat(StateLock.ui);
      _autoRefreshIfStale();
    });
    _lock?.heartbeat(StateLock.ui);
    // Адрес движка поднимается с диска сразу; все обращения к движку ждут
    // этого обещания, а не гонятся с ним наперегонки.
    _engineReady = _loadEngineAddress();
  }

  /// Сохранённый адрес и токен движка применены. Ждут все, кто идёт к нему.
  late final Future<void> _engineReady;

  final SignalAiRepository _repository;
  final NativeBridge _bridge;
  final bool _thinMode;

  bool get thinMode => _thinMode;

  /// Данные — из макета, а не с рынка.
  ///
  /// Демо-сборка нужна, чтобы смотреть интерфейс без движка и без сети. Но
  /// приложение отправляет заявки, и цифры в нём читаются как торговые: тикер,
  /// уровни, риск в рублях выглядят одинаково независимо от происхождения.
  /// Поэтому демо-режим обязан назвать себя на экране — иначе выдуманный
  /// уровень входа однажды будет исполнен руками.
  bool get demoData => _repository is DemoRepository;

  /// Блокировка состояния. null — режим без фонового контура (демо).
  StateLock? get _lock {
    final repository = _repository;
    return repository is LocalAnalysisRepository ? repository.stateLock : null;
  }
  Timer? _autoRefreshTimer;
  DateTime? _digestFetchedAt;
  bool _optimizationTriggered = false;
  bool _optimizing = false;

  /// Идёт ли подбор параметров (walk-forward).
  bool get optimizing => _optimizing;

  AppTab _tab = AppTab.ideas;

  /// Раздел и выбранная пилюля версии 3.
  AppRoute _route = const AppRoute(AppSection.today);

  CapitalState? _capital;
  bool _capitalLoading = false;
  String? _capitalNote;

  String? _selectedSignalId;
  bool _sheetOpen = false;
  String? _toast;
  ToastTone _toastTone = ToastTone.success;
  Timer? _toastTimer;
  bool _backtestRunning = false;
  bool _confirming = false;

  DailyDigest? _digest;

  /// Движок §18. Считает он, приложение показывает.
  final EngineClient _engine;
  /// До первого запроса лента не «пустая», а **неопрошенная**. Разница
  /// видна на экране: пустой список читается как «сетапов нет».
  EngineIdeas _engineIdeas =
      const EngineIdeas.unavailable('Движок ещё не опрошен.');
  Map<String, dynamic>? _engineDataStatus;

  InvestDigest? _invest;
  bool _investLoading = false;
  Object? _investError;
  bool _investBacktestRunning = false;
  TradesSummary? _trades;
  StrategiesSnapshot? _strategies;
  SettingsSnapshot? _settings;
  Object? _error;

  bool _digestLoading = false;
  Object? _digestError;
  String? _analysisStage;

  AppTab get tab => _tab;
  bool get sheetOpen => _sheetOpen;
  String? get toast => _toast;

  /// Чем закончилось действие. Отказ не должен выглядеть успехом: через
  /// один и тот же тост идут «Ордер отправлен» и «Проверка не пропустила».
  ToastTone get toastTone => _toastTone;
  bool get backtestRunning => _backtestRunning;
  bool get confirming => _confirming;
  DailyDigest? get digest => _digest;
  InvestDigest? get invest => _invest;
  bool get investLoading => _investLoading;
  Object? get investError => _investError;
  bool get investBacktestRunning => _investBacktestRunning;
  String get investErrorText =>
      _investError == null ? '' : _errorText(_investError!);

  /// Раздел «Инвест» репозитория. null — режим без него (демо, сервер).
  InvestDesk? get investDesk {
    final repository = _repository;
    return repository is InvestDesk ? repository as InvestDesk : null;
  }
  TradesSummary? get trades => _trades;
  StrategiesSnapshot? get strategies => _strategies;
  SettingsSnapshot? get settings => _settings;
  Object? get error => _error;

  /// Идёт ли расчёт дайджеста (анализ на устройстве или запрос к серверу).
  bool get digestLoading => _digestLoading;

  /// Ошибка расчёта дайджеста — оболочка при этом остаётся рабочей.
  Object? get digestError => _digestError;

  /// Текущая стадия длинного расчёта («Анализ SiU6…») — для индикатора.
  String? get analysisStage => _analysisStage;

  /// Текст ошибки дайджеста для экрана — тот же словарь, что у тостов.
  String get digestErrorText =>
      _digestError == null ? '' : _errorText(_digestError!);

  /// Быстрая фаза запуска: оболочка ждёт только настройки и справочники.
  /// Дайджест считается отдельно и показывает прогресс, а не заставку.
  bool get isLoading => _settings == null && _error == null;

  /// Открыта ли карточка идеи (детальный экран поверх вкладки «Идеи»).
  bool get isDetailOpen => _selectedSignalId != null;

  /// Текущая идея: выбранная либо первая в дайджесте.
  TradingSignal? get currentSignal {
    final selected = _selectedSignalId;
    final signals = _digest?.signals ?? const <TradingSignal>[];
    if (selected != null) {
      // Выбранная идея важнее любых умолчаний. Раньше при промахе сюда
      // подставлялся первый сигнал дайджеста — и разбор открывался на чужом
      // инструменте: тикер, цена и уровни принадлежали другой сделке.
      for (final signal in signals) {
        if (signal.id == selected) return signal;
      }
      // Идеи приходят с движка, сигналы — с дайджеста, и общих
      // идентификаторов у них нет. Без этого перевода нажатие по карточке
      // движка не открывало ничего: экран разбора требует сигнал.
      for (final idea in _engineIdeas.ideas) {
        if (idea.id == selected) return EngineContract.signalFrom(idea);
      }
      return null;
    }
    // Ничего не выбрано — вторая колонка планшета показывает первую идею
    // дайджеста, чтобы не пустовать.
    return signals.isEmpty ? null : signals.first;
  }

  RiskProfile? get risk => _settings?.risk;

  // ── Идеи по ТЗ v2 ──────────────────────────────────────────────────────

  /// Состояние риск-лимитов ТЗ §20 по журналу бумажных сделок.
  ///
  /// null — настройки ещё не загружены: риск на сделку неизвестен, а без
  /// него перевести результат в R в проценты капитала нельзя.
  RiskCenter? get riskCenter {
    final profile = _settings?.risk;
    if (profile == null) return null;
    final repository = _repository;
    // Режим без журнала бумажных сделок — это не «лимиты неизвестны», а
    // «сделок ещё не было»: пустой журнал и есть честное состояние.
    final ledger = repository is LocalAnalysisRepository
        ? repository.ledger
        : SignalLedger(trades: const [], rejected: const []);
    return RiskCenter.fromLedger(
      ledger,
      now: DateTime.now(),
      riskPerTradePercent: profile.riskPercent,
    );
  }

  /// Идеи по ТЗ: приходят с движка целиком (§18).
  ///
  /// На устройстве они больше не собираются. Прежний мост набивал восемь
  /// факторов ТЗ v2 из шести факторов легаси-скринера, подставляя ноль там,
  /// где измерять было нечем: ликвидность всегда была нулём, событийный риск
  /// всегда единицей, а Вайкоффа не существовало вовсе. Оценка §15.1 состоит
  /// из одиннадцати компонентов и множителя качества данных — собрать её из
  /// того, что скринер не измерял, невозможно, и попытка означала бы показ
  /// чисел, за которыми ничего нет.
  List<Idea> get ideas => _engineIdeas.ideas;

  /// Почему идей нет. null — движок ответил и сетапы есть.
  ///
  /// «Движок недоступен» и «сетапов нет» — разные новости, и различать их
  /// обязано приложение (§24), иначе обрыв связи читается как спокойный день.
  String? get ideasUnavailableReason => _engineIdeas.unavailableReason;

  String? get noSetupsReason => _engineIdeas.noSetupsReason;

  /// Состояние загрузки данных на сервере: сколько инструментов во вселенной,
  /// сколько допущено, насколько свежи бары.
  Map<String, dynamic>? get engineDataStatus => _engineDataStatus;

  // ── Портфель (§6) ──────────────────────────────────────────────────────

  PortfolioState? _portfolio;
  bool _portfolioLoading = false;
  PortfolioRebalance? _portfolioRebalance;
  String? _portfolioRebalanceModelId;
  String? _portfolioRebalanceLoadingModelId;

  /// Пакеты капитала и состояние их сборки. null — ещё не запрашивали.
  PortfolioState? get portfolio => _portfolio;
  bool get portfolioLoading => _portfolioLoading;
  PortfolioRebalance? get portfolioRebalance => _portfolioRebalance;
  String? get portfolioRebalanceModelId => _portfolioRebalanceModelId;
  String? get portfolioRebalanceLoadingModelId => _portfolioRebalanceLoadingModelId;

  /// Запросить пакеты у движка.
  ///
  /// Экран вызывает это при первом показе. Повторные вызовы во время
  /// загрузки игнорируются: перерисовка списка не должна порождать второй
  /// запрос, а пересборка на сервере — операция не бесплатная.
  Future<void> loadPortfolio({bool force = false}) async {
    if (_portfolioLoading) return;
    if (_portfolio != null && !force) return;
    _portfolioLoading = true;
    // Уведомление о начале загрузки может прийти в момент построения
    // экрана — отпускаем кадр, иначе Flutter справедливо ругается на
    // setState во время build.
    await Future<void>.microtask(() {});
    notifyListeners();
    try {
      // Тот же порядок, что у идей: сначала адрес, потом запрос.
      await _engineReady;
      // Снимок счёта уходит до запроса пакетов, а не после: движок считает
      // расхождение по тому, что у него есть на момент вопроса. Иначе
      // ребаланс отвечал бы по вчерашнему составу — или, как было до сих
      // пор, «инвестиционный счёт не подключён» при подключённом счёте.
      await _syncHoldings();
      _portfolio = await _engine.portfolio();
    } finally {
      _portfolioLoading = false;
      notifyListeners();
    }
  }

  /// Сверить счёт именно с выбранным серверным пакетом.
  ///
  /// Снимок текущих позиций отправляется непосредственно перед сравнением.
  /// При быстром переключении пакетов поздний ответ старого запроса
  /// отбрасывается и не может появиться под карточкой нового состава.
  Future<void> loadPortfolioRebalance(
    EnginePackage package, {
    bool force = false,
  }) async {
    final modelId = package.id.trim();
    if (modelId.isEmpty) {
      _portfolioRebalanceModelId = '';
      _portfolioRebalance = const PortfolioRebalance.unavailable(
        'Пакет не выбран.',
      );
      notifyListeners();
      return;
    }
    if (_portfolioRebalanceLoadingModelId == modelId) return;
    if (!force &&
        _portfolioRebalanceModelId == modelId &&
        _portfolioRebalance != null) {
      return;
    }

    _portfolioRebalanceModelId = modelId;
    _portfolioRebalance = null;
    _portfolioRebalanceLoadingModelId = modelId;
    await Future<void>.microtask(() {});
    notifyListeners();
    try {
      await _engineReady;
      await _syncHoldings();
      final result = await _engine.portfolioRebalance(modelId);
      if (_portfolioRebalanceModelId == modelId) {
        _portfolioRebalance = result;
      }
    } finally {
      if (_portfolioRebalanceLoadingModelId == modelId) {
        _portfolioRebalanceLoadingModelId = null;
        notifyListeners();
      }
    }
  }

  // ── Ранние сигналы ──────────────────────────────────────────────────────

  ResearchState? _research;
  bool _researchLoading = false;

  ResearchState? get research => _research;
  bool get researchLoading => _researchLoading;

  /// Загрузить гипотезы и состояние источников.
  ///
  /// Отдельно от пакетов: контуры считаются разными прогонами и падают по
  /// разным причинам. Недоступный источник ранних сигналов не должен
  /// выглядеть как сломанный расчёт пакетов.
  Future<void> loadResearch({bool force = false}) async {
    if (_researchLoading) return;
    if (_research != null && !force) return;
    _researchLoading = true;
    await Future<void>.microtask(() {});
    notifyListeners();
    try {
      await _engineReady;
      _research = await _engine.research();
    } finally {
      _researchLoading = false;
      notifyListeners();
    }
  }

  /// Отправить движку, что лежит на инвестиционном счёте.
  ///
  /// Счёт читает устройство токеном на чтение, который лежит в защищённом
  /// хранилище телефона и на сервер не передаётся: он привязан к
  /// пользователю, а не к счёту, и видит все счета владельца.
  ///
  /// Молча ничего не делает, если токена нет, счёт не выбран или движок не
  /// ответил. Пакеты считаются и без снимка — без него не работает только
  /// ребаланс, и ронять из-за этого весь экран нельзя.
  Future<void> _syncHoldings() async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    try {
      final snapshot = await repository.investHoldings();
      if (snapshot == null) return;
      await _engine.putHoldings(
        accountId: snapshot.accountId,
        title: snapshot.title,
        positions: [for (final h in snapshot.holdings) h.toJson()],
      );
    } on Exception {
      // Снимок — вспомогательный шаг. Его отказ не должен превращаться в
      // «движок не ответил» на экране пакетов.
    }
  }

  /// Обновить выдачу движка.
  ///
  /// В демо-режиме движка нет и быть не должно: идеи берутся из макета. Без
  /// этой ветки демо-сборка показывала «движок не ответил» на всех экранах —
  /// то есть ровно ничего, хотя её единственная задача показать интерфейс.
  Future<void> refreshIdeas() async {
    if (_ideasLoading) return;
    _ideasLoading = true;
    if (demoData) {
      _engineIdeas = EngineIdeas(ideas: DemoIdeas.all(DateTime.now()));
      _ideasFetchedAt = DateTime.now();
      _ideasLoading = false;
      notifyListeners();
      return;
    }
    try {
      // Сохранённый адрес обязан быть применён до первого запроса: иначе
      // холодный старт уходит к движку с пустым адресом и показывает «адрес
      // не задан» при полностью настроенном приложении.
      await _engineReady;
      final fetched = await _engine.today();
      // Одна карточка на инструмент. Три стратегии, посмотревшие на BTCUSDT,
      // это не три сделки — это три мнения об одной, и показывать их рядом
      // значит предлагать войти трижды. Остаётся лучшее мнение; ТЗ §16 того же
      // требует от сервера («до трёх карточек»), но пока он присылает всё,
      // отбор делается здесь.
      final previous = _engineIdeas.ideas;
      _engineIdeas = EngineIdeas(
        ideas: bestPerInstrument(fetched.ideas),
        unavailableReason: fetched.unavailableReason,
        noSetupsReason: fetched.noSetupsReason,
      );
      _engineDataStatus = await _engine.dataStatus();
      // Сделки спрашиваются здесь же: сопровождение живёт на сервере, и
      // карточка позиции обязана обновляться тем же тактом, что и лента. При
      // недоступности движка остаётся `null` — прошлый ответ не затираем,
      // иначе обрыв связи выглядит как «позиции закрылись».
      final trades = await _engine.paperTrades();
      if (trades != null) _serverPaperTrades = trades;
      _ideasFetchedAt = DateTime.now();
      notifyListeners();
      // В production-thin уведомляет один фоновый server snapshot poll. Второй
      // foreground-канал создавал дубли с другими id и снова привязывал
      // доставку к открытому приложению.
      if (!_thinMode) await _notifyNewIdeas(previous, _engineIdeas.ideas);
    } finally {
      _ideasLoading = false;
    }
  }

  bool _ideasLoading = false;
  DateTime? _ideasFetchedAt;

  /// Пуш о новых идеях движка.
  ///
  /// Раньше уведомления слал только скринер на устройстве — по своему,
  /// отдельному дайджесту. Идеи движка, то есть то, что показано на экране
  /// «Идеи», не приводили к пушу вообще: приложение находило сделку и
  /// молчало, пока владелец сам не откроет его.
  ///
  /// Поводов ровно два, и оба про действие, а не про новизну:
  /// появилась сильная идея (балл §14.2 и выше порога показа) и идея дошла
  /// до состояния, в котором нужно решение. Пуш о переходе шлётся даже при
  /// невысоком балле: момент входа не повторяется.
  Future<void> _notifyNewIdeas(List<Idea> previous, List<Idea> current) async {
    if (!pushEnabled || previous.isEmpty && current.isEmpty) return;
    final before = {for (final i in previous) i.id: i};
    var id = 200;
    for (final idea in current) {
      final was = before[idea.id];
      final appeared = was == null && idea.score.value >= _pushScoreFloor;
      final armed = idea.readiness.canAct &&
          idea.actionable &&
          (was == null || !was.readiness.canAct || !was.actionable);
      if (!appeared && !armed) continue;
      final notice = ideaNotice(idea, armed: armed);
      // Без адреса пуш приводит на вчерашний экран: карточку пришлось бы
      // искать руками ровно в тот момент, когда дорога каждая минута.
      await _bridge.notify(
        id: id++,
        title: notice.title,
        body: notice.body,
        payload: notice.payload,
      );
      if (id > 299) id = 200;
    }
  }

  /// Балл, ниже которого новая идея не будит владельца. Порог показа §14.2
  /// плюс запас: идея на 66 баллов — это «есть о чём подумать», а не «звони
  /// брокеру».
  static const _pushScoreFloor = 75;

  /// Включены ли уведомления. Настройка одна на оба контура — скринер на
  /// устройстве и движок: два разных выключателя для одного и того же
  /// «звенеть или нет» владелец не найдёт.
  bool get pushEnabled {
    final repository = _repository;
    if (repository is LocalAnalysisRepository) return repository.pushEnabled;
    // Другого хранилища настроек у уведомлений пока нет; молчать по
    // умолчанию честнее, чем звенеть без спроса.
    return false;
  }

  /// Лучшая идея на инструмент: дальше по конвейеру, при равенстве — выше
  /// балл. Triggered с 76 важнее Watch с 84: у первого есть сделка сейчас,
  /// у второго только контекст.
  static List<Idea> bestPerInstrument(List<Idea> ideas) {
    final best = <String, Idea>{};
    for (final idea in ideas) {
      final current = best[idea.instrumentId];
      if (current == null ||
          IdeaPriority.tier(idea) > IdeaPriority.tier(current) ||
          (IdeaPriority.tier(idea) == IdeaPriority.tier(current) &&
              idea.score.value > current.score.value)) {
        best[idea.instrumentId] = idea;
      }
    }
    return best.values.toList();
  }

  /// Настройки уровня приложения — те, что не принадлежат ни одному
  /// репозиторию. Пока это только адрес движка.
  ///
  /// Подменяется в тестах: порядок «сначала адрес, потом запрос к движку»
  /// проверяется только тогда, когда чтение с диска можно задержать.
  final LocalStore _prefs;

  /// Итог последней проверки связи с движком. null — не проверяли.
  String? _engineProbe;
  String? _engineAuthIssue;
  bool _engineProbing = false;

  String? get engineProbe => _engineProbe;
  String? get engineAuthIssue => _engineAuthIssue;
  bool get engineProbing => _engineProbing;

  /// Адрес движка, по которому приложение ходит за идеями.
  String get engineBaseUrl => ApiConfig.baseUrl;

  /// Задан ли адрес руками, а не сборкой.
  bool get engineFromSettings => ApiConfig.isOverridden;

  /// Чтение сохранённого адреса движка.
  ///
  /// Раньше его никто не ждал: запуск шёл на сборочном адресе, а
  /// сохранённый догонял и перечитывал ленту. Для идей это работало, для
  /// пакетов — нет: раздел «Портфель» спрашивает движок при первом показе,
  /// и если адрес к тому моменту ещё не приехал, состояние «адреса нет»
  /// оставалось до ручного нажатия. Одно и то же приложение при одинаковых
  /// настройках вело себя по-разному в зависимости от того, кто успел
  /// первым.
  ///
  /// Теперь чтение — обещание [_engineReady], которого дожидаются все, кто
  /// собирается к движку. Это одно чтение с диска за запуск, а не задержка
  /// на каждом запросе.
  Future<void> _loadEngineAddress() async {
    final credentials = await restoreEngineRuntime(_prefs, _bridge);
    _engineAuthIssue = credentials.ready ? null : credentials.issue;
  }

  /// Задан ли runtime-токен устройства из Android Keystore.
  bool get engineTokenSet => ApiConfig.deviceToken.isNotEmpty;

  /// Задать адрес движка из «Подключений».
  ///
  /// Пустая строка возвращает приложение к адресу из сборки. Сразу после
  /// записи идеи перечитываются: смена адреса без перезагрузки ленты
  /// выглядела бы как «не сработало».
  Future<void> setEngineBaseUrl(
    String url, {
    String? token,
    String? pairingSessionId,
  }) async {
    final value = url.trim();
    final previousUrl = ApiConfig.baseUrl;
    if (token != null) {
      final entered = token.trim();
      if (entered.isEmpty) {
        // A self-forget remains fail-closed: retain the Keystore bearer and
        // the local enrollment document unless the old server positively
        // confirms revocation (including its explicit idempotent response).
        try {
          if (ApiConfig.deviceToken.isNotEmpty) {
            await forgetEngineDevice(
              _prefs,
              _bridge,
              baseUrl: previousUrl,
            );
          } else {
            await _prefs.writeDurably('engine', {'base_url': value});
          }
          ApiConfig.setBaseUrl(value);
          ApiConfig.setDeviceToken('');
        } on DeviceEnrollmentException catch (error) {
          _engineAuthIssue = error.message;
          showToast(_engineAuthIssue!, tone: ToastTone.failure);
          notifyListeners();
          return;
        }
        ApiConfig.setDeviceToken('');
        _engineAuthIssue = value.isEmpty
            ? null
            : 'Устройство не привязано: задайте токен в «Подключениях».';
      } else {
        // Bootstrap and pairing session are never copied into ApiConfig or
        // the vault.  Replacing a configured server first revokes the old
        // bearer at its original scope, preventing a credential scope rebind.
        if (ApiConfig.deviceToken.isNotEmpty) {
          try {
            await forgetEngineDevice(
              _prefs,
              _bridge,
              baseUrl: previousUrl,
            );
            ApiConfig.setDeviceToken('');
          } on DeviceEnrollmentException catch (error) {
            _engineAuthIssue = error.message;
            showToast(_engineAuthIssue!, tone: ToastTone.failure);
            notifyListeners();
            return;
          }
        }
        ApiConfig.setBaseUrl(value);
        ApiConfig.setDeviceToken('');
        try {
          final issued = await pairAndStoreEngineDevice(
            _prefs,
            _bridge,
            baseUrl: ApiConfig.baseUrl,
            bootstrapToken: entered,
            pairingSessionId: pairingSessionId?.trim() ?? '',
          );
          ApiConfig.setDeviceToken(issued.deviceToken);
          _engineAuthIssue = null;
        } on DeviceEnrollmentException catch (error) {
          _engineAuthIssue = error.message;
          showToast(_engineAuthIssue!, tone: ToastTone.failure);
        }
      }
    } else {
      if (ApiConfig.deviceToken.isNotEmpty) {
        _engineAuthIssue =
            'Сначала отзовите привязку устройства: адрес нельзя сменить с действующим токеном.';
        showToast(_engineAuthIssue!, tone: ToastTone.failure);
        notifyListeners();
        return;
      }
      ApiConfig.setBaseUrl(value);
      final saved = await _prefs.read('engine') ?? <String, dynamic>{};
      await _prefs.writeDurably('engine', {
        'base_url': value,
        if (saved['device_enrollment_v1'] == true)
          'device_enrollment_v1': true,
        if (saved['device_id'] is String) 'device_id': saved['device_id'],
      });
    }
    _engineProbe = null;
    // Ответ прежнего адреса выбрасывается целиком, включая «адреса нет».
    // Оставить его значило бы показывать на «Портфеле» отказ старого
    // сервера после переезда на новый — до ручного нажатия.
    _portfolio = null;
    notifyListeners();
    await refreshIdeas();
    await loadPortfolio(force: true);
  }

  /// Rotate the current active-device bearer and replace the single Keystore
  /// entry only after the server returns a distinct successor generation.
  Future<void> rotateEngineDeviceToken() async {
    try {
      final issued = await rotateAndStoreEngineDevice(
        _prefs,
        _bridge,
        baseUrl: ApiConfig.baseUrl,
      );
      ApiConfig.setDeviceToken(issued.deviceToken);
      _engineAuthIssue = null;
      _engineProbe = null;
      notifyListeners();
    } on DeviceEnrollmentException catch (error) {
      _engineAuthIssue = error.message;
      showToast(_engineAuthIssue!, tone: ToastTone.failure);
      notifyListeners();
    }
  }

  /// Проверить связь с движком и показать, что он ответил.
  ///
  /// Отдельно от ленты идей: пустая лента и недоступный сервер — разные
  /// новости, и проверять их надо разными способами (§24).
  Future<void> probeEngine() async {
    if (_engineProbing) return;
    _engineProbing = true;
    _engineProbe = null;
    notifyListeners();
    if (ApiConfig.baseUrl.isEmpty) {
      _engineProbe = 'Адрес не задан — спрашивать нечего.';
    } else {
      final health = await _engine.health();
      _engineProbe = health == null
          ? 'Движок не ответил по адресу ${ApiConfig.baseUrl}.'
          : 'Ответил: версия ${health['engine_version'] ?? '—'}, '
              'режим ${health['execution_mode'] ?? '—'}, '
              'состояние ${health['status'] ?? '—'}.';
    }
    _engineProbing = false;
    notifyListeners();
  }

  /// Свечи production-идеи приходят только от движка.
  ///
  /// Ключ кэша — идея **и таймфрейм**. Раньше кэш был по идее, и переключать
  /// картинку было нечем: кнопки 1d/4h/1h стояли на экране, но нажимались
  /// впустую, потому что за ними лежал ровно один загруженный ряд.
  /// Прямой market source допустим только как явно подставленная demo/test
  /// fixture. Production server idea никогда не обращается к бирже с телефона.
  final IdeaChartSource? _demoChartSource;
  final Map<String, SignalChart> _ideaCharts = {};
  final Set<String> _ideaChartsAsked = {};
  final Set<String> _ideaChartsFailed = {};
  final Map<String, String> _ideaTimeframe = {};

  static String _chartKey(String ideaId, String timeframe) => '$ideaId|$timeframe';

  /// Таймфрейм сетапа — тот, на котором идея построена и на котором лежит её
  /// разметка. Контекстный слишком крупен для зоны входа, триггерный слишком
  /// мелок для структуры.
  static String setupTimeframe(Idea idea) => idea.timeframes.length >= 2
      ? idea.timeframes[1]
      : (idea.timeframes.isEmpty ? '1h' : idea.timeframes.first);

  /// Показываемый сейчас таймфрейм идеи.
  String ideaTimeframe(Idea idea) =>
      _ideaTimeframe[idea.id] ?? setupTimeframe(idea);

  /// График идеи на выбранном таймфрейме. null — ещё не загружен или
  /// источник его не отдал.
  SignalChart? ideaChart(String ideaId, {String timeframe = ''}) {
    final tf = timeframe.isNotEmpty ? timeframe : _ideaTimeframe[ideaId];
    if (tf == null) {
      // Таймфрейм не выбирали — отдаём то единственное, что загружено.
      for (final entry in _ideaCharts.entries) {
        if (entry.key.startsWith('$ideaId|')) return entry.value;
      }
      return null;
    }
    return _ideaCharts[_chartKey(ideaId, tf)];
  }

  /// Свечи запрошены, но ещё не пришли. Нужно экрану: без этого переключение
  /// таймфрейма выглядит как поломка — картинка пропала и ничего не сказано.
  bool ideaChartLoading(Idea idea) {
    // Таймфреймы ещё не приехали — значит ждём полную карточку. Это тоже
    // загрузка, и молчать о ней нельзя: иначе на месте графика висит
    // «недоступен», хотя запрос идёт.
    if (idea.timeframes.isEmpty) return true;
    final key = _chartKey(idea.id, ideaTimeframe(idea));
    return _ideaChartsAsked.contains(key) &&
        !_ideaCharts.containsKey(key) &&
        !_ideaChartsFailed.contains(key);
  }

  /// Источник не дал свечей этого таймфрейма. Отличается от «ещё грузим».
  bool ideaChartFailed(Idea idea) =>
      _ideaChartsFailed.contains(_chartKey(idea.id, ideaTimeframe(idea)));

  /// Почему свечей нет. Пусто — причина не записана.
  ///
  /// Заглушка «свечей источник не дал» выглядела одинаково и когда биржа
  /// закрыта для страны владельца, и когда движок просто не знает такого
  /// инструмента. Это разные вещи и разные действия, а оба перехвата были
  /// немыми — владелец получал «живой график недоступен» без единого слова
  /// о том, почему.
  final Map<String, String> _chartFailureReason = {};

  String ideaChartFailureReason(Idea idea) =>
      _chartFailureReason[_chartKey(idea.id, ideaTimeframe(idea))] ?? '';

  /// Переключить таймфрейм графика идеи.
  void selectIdeaTimeframe(Idea idea, String timeframe) {
    if (timeframe.isEmpty || ideaTimeframe(idea) == timeframe) return;
    _ideaTimeframe[idea.id] = timeframe;
    notifyListeners();
    loadIdeaChart(idea);
  }

  /// Загрузить свечи для идеи на текущем таймфрейме.
  /// Успешный или уже идущий запрос дедуплицируется; после зафиксированного
  /// отказа явный повтор разрешён и очищает старое сообщение об ошибке.
  Future<void> loadIdeaChart(Idea idea) async {
    // Пока таймфреймы неизвестны, грузить нечего.
    //
    // Лента отдаёт сводки без context/setup/trigger, и запасной «1h»
    // приводил к тому, что первым загружался часовой ряд, а после
    // подгрузки полной карточки сетапным оказывался 4h — и на экране
    // оставались часовые свечи, которые никто уже не переспрашивал.
    // Отметки «спрошено» здесь тоже не ставим: запрос ещё предстоит.
    if (idea.timeframes.isEmpty) return;
    final timeframe = ideaTimeframe(idea);
    final key = _chartKey(idea.id, timeframe);
    final failed = _ideaChartsFailed.contains(key);
    if (_ideaChartsAsked.contains(key) && !failed) return;
    _ideaChartsAsked.add(key);
    if (failed) {
      _ideaChartsFailed.remove(key);
      _chartFailureReason.remove(key);
    }
    // Уступаем микрозадачу перед любой работой. Вызов приходит из `build`
    // разбора, а демо-режим отвечает без единого `await` — и
    // `notifyListeners` попадал внутрь построения дерева: «setState() called
    // during build». Микрозадача, а не таймер: таймер переживает тест и
    // роняет его на «A Timer is still pending».
    await Future<void>.microtask(() {});
    if (failed) notifyListeners();
    // В production direct market fallback запрещён: он возвращал на телефон
    // дублирующие клиенты, геоблоки и сетевые вылеты. Демо использует только
    // детерминированную fixture (либо явно подставленный тестовый источник).
    final reasons = <String>[];
    SignalChart? chart;
    if (demoData) {
      chart = DemoIdeas.chartFor(idea);
      if (chart == null && _demoChartSource != null) {
        chart = await _demoChartSource.load(
          idea,
          timeframe: timeframe,
          onFailure: reasons.add,
        );
      }
    } else {
      // Detail может назвать сетап `H4`, а bars принимает `4h`. Сначала
      // нормализуем алиас и исчерпываем серверную цепочку setup → 4H → 1H →
      // D1. Промежуточный отказ не становится заглушкой; итоговая причина
      // появляется только после исчерпания серверной цепочки.
      chart = await _engine.barsWithFallback(
        idea.instrumentId,
        setupTimeframe: timeframe,
        onFailure: reasons.add,
      );
    }
    if (chart == null) {
      _ideaChartsFailed.add(key);
      if (reasons.isNotEmpty) {
        _chartFailureReason[key] = reasons.join(' · ');
      }
      notifyListeners();
      return;
    }
    _chartFailureReason.remove(key);
    // Ряд кладётся под тем таймфреймом, который реально нарисован, а не под
    // запрошенным: server fallback может вернуть H1 вместо H4. Подписать его
    // «H4» значило бы соврать на картинке, по которой принимают решение.
    _ideaCharts[_chartKey(idea.id, chart.timeframeLabel)] = chart;
    if (chart.timeframeLabel != timeframe) _ideaCharts[key] = chart;
    notifyListeners();
  }

  /// Полная карточка идеи: план, разбор оценки, доказательства, разметка.
  Future<Idea?> ideaDetail(String id) async {
    if (demoData) {
      return _engineIdeas.ideas.where((i) => i.id == id).firstOrNull;
    }
    return _engine.detail(id);
  }

  /// Показатели журнала (ТЗ §12.1). null — журнала нет вовсе.
  /// Открытые и выставленные бумажные сделки.
  ///
  /// Нужны не только журналу. «Идеи → В работе» показывали лишь идеи движка
  /// в состоянии Active, а бумажная позиция живёт в журнале сделок — и экран
  /// писал «открытых позиций нет», пока в журнале висела открытая позиция по
  /// тому же инструменту. Три экрана, три разных ответа на один вопрос.
  List<PaperTrade> get openPaperTrades {
    if (_thinMode) return const [];
    final repository = _repository;
    return repository is LocalAnalysisRepository
        ? repository.ledger.openOrPending
        : const [];
  }

  /// Сделки, которые ведёт сервер. Пусто — движок ещё не отвечал.
  List<PaperPosition> _serverPaperTrades = const [];

  /// Есть ли на что открыть разбор по этому идентификатору.
  ///
  /// Идеи движка и сигналы дайджеста — два разных множества, и карточка
  /// позиции смотрела только в первое. Сделка, заведённая скринером на
  /// устройстве, честно несла идентификатор своего сигнала, но подписывалась
  /// «идеи за ней нет в текущей выдаче» и не нажималась: владелец увидел это
  /// как «идея появилась сразу в журнале, и написано, что идей по ней нет».
  bool canOpenSignal(String id, {bool fromServer = false}) {
    if (id.isEmpty) return false;
    if (_engineIdeas.ideas.any((i) => i.id == id)) return true;
    if ((_digest?.signals ?? const <TradingSignal>[]).any((s) => s.id == id)) {
      return true;
    }
    // Сделка с сервера ссылается на серверную идею. Её может не быть в
    // ленте — лента показывает три карточки дня, — но разбор существует и
    // догружается по идентификатору (`_hydrateIdea`). «Нет в текущей
    // выдаче» — утверждение про ленту, а владелец читал его как «идеи не
    // существует» и упирался в нерабочую карточку.
    return fromServer;
  }

  /// Открытые позиции для экрана: сервер, а при его молчании — устройство.
  ///
  /// Приоритет у сервера не из вкусовщины. Устройство брало свечи только у
  /// биржи напрямую, Bybit отвечает телефону владельца `403 — CloudFront
  /// блокирует доступ из вашей страны`, свечей нет, `reconcile` не
  /// вызывается ни разу — и позиция замирает навсегда, при этом выглядя
  /// живой. Так BTCUSDT провисел несколько дней на «взято тейков: 2 из 3».
  /// Сервер до биржи доходит и считает, когда телефон выключен.
  ///
  /// Местные сделки не выбрасываются: журнал ведёт и скринер на устройстве,
  /// у которого свои сигналы, и без них экран потерял бы часть позиций.
  /// Совпадения снимаются по инструменту — одна и та же сделка, посчитанная
  /// дважды, на экране должна быть одна, и показывается серверная.
  List<PaperPosition> get paperPositions {
    final server = _serverPaperTrades;
    if (_thinMode) return server;
    final seen = {for (final p in server) p.symbol};
    return [
      ...server,
      for (final trade in openPaperTrades)
        if (!seen.contains(trade.symbol)) PaperPosition.fromLedger(trade),
    ];
  }

  JournalMetrics? get journalMetrics {
    final repository = _repository;
    return repository is LocalAnalysisRepository
        ? JournalMetrics.fromLedger(repository.ledger)
        : null;
  }

  /// Пропущенные идеи с причинами (ТЗ §12).
  List<SkipRecord> get skips {
    final repository = _repository;
    return repository is LocalAnalysisRepository
        ? repository.skips
        : const <SkipRecord>[];
  }

  /// Ведётся ли журнал решений. Без него пропуск некуда записать, и кнопку
  /// «пропустить с причиной» показывать нечестно.
  bool get skipJournalAvailable => _repository is LocalAnalysisRepository;

  bool canRejectIdea(Idea idea) =>
      (!demoData && _engineIdeas.ideas.any((item) => item.id == idea.id)) ||
      skipJournalAvailable;

  /// Записать пропуск идеи с причиной из справочника (ТЗ §12).
  Future<void> skipIdea(
    Idea idea, {
    required SkipReason reason,
    String comment = '',
  }) async {
    // Идея движка отклоняется там же, где живёт её lifecycle. Локальная
    // запись рядом создавала второй журнал и оставляла серверную карточку
    // actionable после того, как владелец уже нажал «Пропустить».
    if (!demoData && _engineIdeas.ideas.any((item) => item.id == idea.id)) {
      try {
        await _engine.rejectIdea(
          idea.id,
          reason: reason.code,
          comment: comment,
        );
        _engineIdeas = EngineIdeas(
          ideas: [for (final item in _engineIdeas.ideas) if (item.id != idea.id) item],
          unavailableReason: _engineIdeas.unavailableReason,
          noSetupsReason: _engineIdeas.noSetupsReason,
        );
        _selectedSignalId = null;
        _sheetOpen = false;
        showToast('Идея отклонена: ${reason.label}');
      } catch (e) {
        showError(e);
      }
      notifyListeners();
      return;
    }

    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    await repository.recordSkip(
      SkipRecord.of(idea, reason: reason, comment: comment, at: DateTime.now()),
    );
    _selectedSignalId = null;
    showToast('Пропуск записан: ${reason.label}');
    notifyListeners();
  }

  /// Идея, открытая в разборе.
  Idea? get currentIdea {
    final list = ideas;
    if (list.isEmpty) return null;
    final selected = _selectedSignalId;
    if (selected == null) return list.first;
    // Выбранный локальный сигнал не должен получать план первой серверной
    // идеи. Это разные множества и разные контуры исполнения.
    return list.where((i) => i.id == selected).firstOrNull;
  }

  /// Запуск: сначала быстрые данные (настройки, стратегии, сделки) — оболочка
  /// появляется сразу; затем дайджест с живым прогрессом расчёта.
  Future<void> load() async {
    try {
      _error = null;
      final results = await Future.wait([
        _repository.fetchTrades(),
        _repository.fetchStrategies(),
        _repository.fetchSettings(),
      ]);
      _trades = results[0] as TradesSummary;
      _strategies = results[1] as StrategiesSnapshot;
      _settings = results[2] as SettingsSnapshot;
    } catch (e) {
      _error = e;
      notifyListeners();
      return;
    }
    notifyListeners();
    // Стартовый раздел подгружается так же, как любой другой. Без этого
    // «Сегодня» — единственный экран, который никто не инициализирует:
    // `_prefetchForRoute` висел только на переходах, а на «Сегодня»
    // приложение уже стоит в момент запуска. Ждём здесь, а не отпускаем в
    // фон: книга читается с диска, это быстро, и следующий кадр должен
    // показать капитал, а не пустой экран.
    await refreshCapital();
    // Идеи приходят с движка §18 и запрашиваются здесь же: без этого лента
    // осталась бы в состоянии «ещё не спрашивали» до первого ручного
    // обновления, и владелец увидел бы пустой экран без объяснения.
    await refreshIdeas();
    if (!_thinMode) await refreshDigest();
    // Уведомление, по которому приложение и запустили. Забирается после
    // первой загрузки: открывать разбор идеи, которой ещё нет в памяти,
    // значит показать пустой экран вместо той самой идеи.
    await openFromNotification();
  }

  /// Открыть то, по чему нажали в уведомлении.
  ///
  /// Пуш без адреса приводит владельца на тот экран, где он был вчера, и
  /// заставляет искать идею руками — то есть перестаёт работать ровно там,
  /// ради чего послан. Адрес отдаётся системой один раз, поэтому повторный
  /// возврат в приложение ничего не открывает.
  Future<void> openFromNotification() async {
    final payload = await _bridge.takeLaunchPayload();
    if (payload.isEmpty) return;
    final id = payload.startsWith(notificationIdeaPrefix)
        ? payload.substring(notificationIdeaPrefix.length)
        : payload;
    if (id.isEmpty) return;
    goSection(AppSection.ideas);
    openSignal(id);
  }

  /// Префикс адреса идеи в уведомлении. Не голый идентификатор: адреса
  /// появятся и у других экранов, а разбирать их по форме строки — способ
  /// однажды открыть сделку вместо пакета.
  static const notificationIdeaPrefix = 'idea:';

  /// Пересчёт идей. [force] — игнорировать свежий кэш (кнопка «Пересчитать»);
  /// без него репозиторий вправе вернуть недавний результат мгновенно.
  Future<void> refreshDigest({bool force = false}) async {
    if (_thinMode) {
      await refreshIdeas();
      return;
    }
    // Кнопка не должна быть немой: если расчёт уже идёт, честно об этом
    // сказать, а не проглотить нажатие.
    if (_digestLoading) {
      showToast(
        'Расчёт уже идёт${_analysisStage == null ? '' : ': $_analysisStage'}',
        tone: ToastTone.warning,
      );
      return;
    }
    _digestLoading = true;
    _digestError = null;
    final previous = _digest;
    final repository = _repository;
    if (repository is ProgressReporting) {
      (repository as ProgressReporting).onProgress = (stage) {
        _analysisStage = stage;
        notifyListeners();
      };
    }
    notifyListeners();
    var succeeded = false;
    try {
      _digest = await _repository.fetchDigest(force: force);
      _digestFetchedAt = DateTime.now();
      succeeded = true;
      _failedRefreshes = 0;
    } catch (e) {
      _digestError = e;
      _failedRefreshes++;
    } finally {
      _digestLoading = false;
      _analysisStage = null;
      if (repository is ProgressReporting) {
        (repository as ProgressReporting).onProgress = null;
      }
      notifyListeners();
    }

    // Всё, что идёт после дайджеста, живёт отдельно: сбой журнала или
    // уведомления не должен превращаться в «данные бирж недоступны».
    if (!succeeded) return;
    try {
      _trades = await _repository.fetchTrades();
      await _notifyNewSignals(previous, _digest);
      _maybeScheduleOptimization();
    } on Object {
      // Вторичные шаги — не повод показывать ошибку расчёта.
    }
    notifyListeners();
  }

  /// Сколько пересчётов подряд не удалось — по нему растёт пауза до повтора.
  int _failedRefreshes = 0;

  /// Автопересчёт по закрытию часового бара: смысл терминала — следить за
  /// рынком, а не показывать утренний снимок весь день.
  ///
  /// Срабатывает в :01 после нового часа — тот же каденс, что у бэктеста
  /// (оценка каждого закрытого бара): живой контур и прогон делают одно и
  /// то же, иначе статистика прогона мерила бы другую стратегию.
  void _autoRefreshIfStale() {
    if (_thinMode) {
      final fetchedAt = _ideasFetchedAt;
      if (!_ideasLoading &&
          (fetchedAt == null ||
              DateTime.now().difference(fetchedAt) >=
                  const Duration(minutes: 15))) {
        unawaited(refreshIdeas());
      }
      return;
    }
    if (_digestLoading || _backtestRunning || _optimizing) return;
    final now = DateTime.now();

    // Расчёт не удался — повторяем с нарастающей паузой (1 → 5 → 15 минут),
    // а не ждём, пока владелец сам нажмёт кнопку. Раньше после первого
    // провала автоповтора не было никогда.
    if (_failedRefreshes > 0) {
      final wait = switch (_failedRefreshes) {
        1 => const Duration(minutes: 1),
        2 => const Duration(minutes: 5),
        _ => const Duration(minutes: 15),
      };
      final since = _lastAttemptAt;
      if (since == null || now.difference(since) >= wait) {
        _lastAttemptAt = now;
        refreshDigest(force: true);
      }
      return;
    }

    final at = _digestFetchedAt;
    if (_digest == null || at == null) return;
    final barClosed =
        DateTime(now.year, now.month, now.day, now.hour)
            .isAfter(DateTime(at.year, at.month, at.day, at.hour));
    if (!barClosed || now.minute < 1) return;
    if (now.difference(at) < const Duration(minutes: 5)) return;
    _lastAttemptAt = now;
    refreshDigest(force: true);
  }

  DateTime? _lastAttemptAt;

  /// Локальный пуш о новых сильных сигналах (score ≥ 75), если пуш включён.
  Future<void> _notifyNewSignals(DailyDigest? previous, DailyDigest? current) async {
    if (previous == null || current == null) return;
    final repository = _repository;
    final pushEnabled = repository is LocalAnalysisRepository && repository.pushEnabled;
    if (!pushEnabled) return;

    final known = {for (final s in previous.signals) s.symbol};
    var id = 100;
    for (final signal in current.signals) {
      if (signal.score < 75 || known.contains(signal.symbol)) continue;
      final notice = signalNotice(signal);
      await _bridge.notify(
        id: id++,
        title: notice.title,
        body: notice.body,
        payload: notice.payload,
      );
    }
  }

  /// Автозапуск walk-forward оптимизации, когда пришёл срок (раз в неделю).
  /// Идёт в фоне после дайджеста и не мешает пользоваться приложением.
  void _maybeScheduleOptimization() {
    if (_thinMode) return;
    final repository = _repository;
    if (repository is! ParameterOptimizing) return;
    if (_optimizationTriggered || !(repository as ParameterOptimizing).optimizationDue) {
      return;
    }
    _optimizationTriggered = true;
    // ignore: discarded_futures — сознательный фон: итог придёт тостом.
    runOptimization(auto: true);
  }

  /// Подбор параметров стратегий walk-forward прогоном.
  Future<void> runOptimization({bool auto = false}) async {
    if (_thinMode) {
      if (!auto) {
        showToast(
          'В thin-режиме локальная оптимизация отключена',
          tone: ToastTone.warning,
        );
      }
      return;
    }
    final repository = _repository;
    if (repository is! ParameterOptimizing || _optimizing || _backtestRunning) return;
    _optimizing = true;
    if (repository is ProgressReporting) {
      (repository as ProgressReporting).onProgress = (stage) {
        _analysisStage = stage;
        notifyListeners();
      };
    }
    notifyListeners();
    try {
      final note = await (repository as ParameterOptimizing).optimizeParameters();
      _strategies = await _repository.fetchStrategies();
      showToast(auto ? 'Недельная оптимизация: $note' : note);
    } catch (e) {
      if (!auto) showError(e);
    } finally {
      _optimizing = false;
      _analysisStage = null;
      if (repository is ProgressReporting) {
        (repository as ProgressReporting).onProgress = null;
      }
      notifyListeners();
    }
  }

  // ── Навигация ──────────────────────────────────────────────────────────

  void goTab(AppTab tab) {
    _tab = tab;
    _selectedSignalId = null;
    _sheetOpen = false;
    notifyListeners();
    // «Инвест» подгружается лениво: кэш мгновенно, пересчёт — если ночь
    // прошла, а скана ещё не было.
    if (tab == AppTab.invest && _invest == null) refreshInvest();
  }

  // ── Навигация версии 3 ─────────────────────────────────────────────────

  AppRoute get route => _route;
  AppSection get section => _route.section;
  int get pill => _route.pill;

  /// Переход в раздел. Пилюля сбрасывается на первую, если раздел меняется, —
  /// возвращаться в «Книгу», потому что там были в прошлый раз, значит терять
  /// три секунды на понимание, куда попал.
  void goSection(AppSection section) {
    if (_route.section == section) return;
    _route = AppRoute(section);
    _selectedSignalId = null;
    _sheetOpen = false;
    _syncLegacyTab();
    notifyListeners();
    _prefetchForRoute();
  }

  void goPill(int index) {
    if (_route.pill == index) return;
    _route = _route.withPill(index);
    _selectedSignalId = null;
    _syncLegacyTab();
    notifyListeners();
    _prefetchForRoute();
  }

  /// Старые экраны живут внутри новых разделов и продолжают спрашивать
  /// [AppTab]. Пока они не переписаны целиком, вкладка держится в
  /// соответствии с маршрутом — так не появляется второй источник истины.
  void _syncLegacyTab() {
    _tab = switch (_route.section) {
      AppSection.today => AppTab.ideas,
      AppSection.portfolio => AppTab.trades,
      AppSection.ideas => AppTab.ideas,
      AppSection.journal => AppTab.trades,
      AppSection.settings =>
        _route.pill == SettingsPill.strategies.index
            ? AppTab.strategies
            : AppTab.settings,
    };
  }

  void _prefetchForRoute() {
    if (_route.section == AppSection.today ||
        _route.section == AppSection.portfolio) {
      refreshCapital();
    }
    if (!_thinMode &&
        _route.section == AppSection.portfolio &&
        _route.pill == PortfolioPill.accounts.index) {
      refreshVenues();
    }
    if (!_thinMode &&
        _route.section == AppSection.settings &&
        _route.pill == SettingsPill.connections.index) {
      refreshVenues();
    }
  }

  // ── Книга капитала ─────────────────────────────────────────────────────

  /// Книга: null — режим без учёта (демо-репозиторий).
  CapitalDesk? get capitalDesk {
    final repository = _repository;
    return repository is CapitalKeeper ? (repository as CapitalKeeper).capital : null;
  }

  CapitalState? get capital => _capital;
  bool get capitalLoading => _capitalLoading;

  /// Итог последней синхронизации с площадками.
  String? get capitalNote => _capitalNote;

  /// Пересчитывает состояние капитала из книги.
  Future<void> refreshCapital({bool sync = false}) async {
    final repository = _repository;
    final desk = capitalDesk;
    if (desk == null || repository is! CapitalKeeper || _capitalLoading) return;
    _capitalLoading = true;
    notifyListeners();
    try {
      await desk.load();
      if (sync) {
        _capitalNote = await (repository as CapitalKeeper).syncCapital();
      }
      final state = await desk.state();
      // Отметка капитала на сегодня: кривая и дельты строятся из отметок, а
      // не пересчётом прошлого — цен вчерашнего дня никто не хранит, и такой
      // пересчёт врал бы ровно на движение рынка.
      await desk.markEquity(state);
      _capital = await desk.state();
    } catch (e) {
      _capitalNote = 'синхронизация не удалась: $e';
    } finally {
      _capitalLoading = false;
      notifyListeners();
    }
  }

  /// Ручная операция в книгу. Возвращает true, если запись добавлена.
  ///
  /// Ручной ввод — не костыль, а обязательная часть модели: банковский
  /// резерв, перевод между площадками и дивиденд, который брокер показал
  /// только в отчёте, иначе в капитал не попадут никогда.
  Future<bool> recordOperation({
    required LedgerEventType type,
    required String accountId,
    required Money cashImpact,
    DateTime? at,
    String? instrument,
    Quantity? quantity,
    Money? price,
    Contour? contour,
    String? note,
  }) async {
    final desk = capitalDesk;
    if (desk == null) return false;
    final now = DateTime.now().toUtc();
    final added = await desk.record([
      LedgerEvent(
        id: 'manual-${now.microsecondsSinceEpoch}',
        type: type,
        effectiveAt: (at ?? now).toUtc(),
        receivedAt: now,
        accountId: accountId,
        cashImpact: cashImpact,
        instrument: instrument,
        quantity: quantity,
        price: price,
        contour: contour,
        source: LedgerSource.manual,
        reconcile: ReconcileStatus.manual,
        note: note,
      ),
    ]);
    if (added > 0) {
      _capital = await desk.state();
      showToast('Операция записана в книгу');
      notifyListeners();
    }
    return added > 0;
  }

  /// Добавляет счёт, который брокер не отдаёт: банковский резерв, кошелёк.
  Future<void> addAccount(Account account) async {
    final desk = capitalDesk;
    if (desk == null) return;
    await desk.saveAccounts([...desk.accounts, account]);
    _capital = await desk.state();
    notifyListeners();
  }

  /// Влияние текущей идеи на портфель — для шита подтверждения.
  ///
  /// Считается из книги и открытых позиций, а не из допущений: одна и та же
  /// сделка приемлема на пустом счёте и недопустима, когда риск уже у лимита.
  PortfolioImpact? get currentImpact {
    final signal = currentSignal;
    final profile = risk;
    if (signal == null || profile == null) return null;

    final state = _capital;
    final equity = state == null || state.isEmpty
        ? Money.of(profile.deposit, Currency.rub)
        : state.totalEquity;
    final perTrade = Money.of(profile.riskRub, Currency.rub);

    // Открытый риск считаем по числу уже работающих идей: точный расчёт по
    // расстоянию до стопа требует стопов у всех позиций, а их у брокера может
    // не быть — и это отдельная проблема, которую видно в очереди решений.
    final working = (_digest?.signals ?? const <TradingSignal>[])
        .where((s) => s.status.isWorking)
        .length;
    final openRisk = perTrade.scaleBy(working.toDouble());

    const limits = RiskLimits();
    return PortfolioImpact.compute(
      riskPerTrade: perTrade,
      openRisk: openRisk,
      equity: equity,
      // Предел открытого риска: столько же процентов капитала, сколько
      // допускает риск на сделку, умноженное на число одновременных сделок.
      limitPercent: profile.riskPercent * limits.maxConcurrent,
      openPositions: working,
      maxPositions: limits.maxConcurrent,
      correlated: _digest?.signals.any((s) =>
              s.id != signal.id &&
              s.status.isWorking &&
              s.correlationGroup != null &&
              s.correlationGroup == signal.correlationGroup) ??
          false,
    );
  }

  // ── Пакеты капитала ────────────────────────────────────────────────────

  final Map<String, PackageBacktest> _packageHistory = {};
  final Set<String> _packageLoading = {};

  /// Замыслы пакетов.
  /// Выбранный горизонт портфеля (ТЗ §7).
  PackageHorizon get packageHorizon => _packageHorizon;
  PackageHorizon _packageHorizon = PackageHorizon.fivePlus;

  void setPackageHorizon(PackageHorizon horizon) {
    if (_packageHorizon == horizon) return;
    _packageHorizon = horizon;
    notifyListeners();
  }

  /// Пакеты выбранного горизонта — по одному на профиль риска.
  List<PackagePlan> get packagePlans => PackagePlan.forHorizon(_packageHorizon);

  /// Историческая симуляция пакета. null — ещё не считали.
  PackageBacktest? packageHistory(String id) => _packageHistory[id];

  bool packageLoading(String id) => _packageLoading.contains(id);

  /// Считает историю пакета по реальным сериям.
  Future<void> loadPackageHistory(PackagePlan plan) async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    if (!_packageLoading.add(plan.id)) return;
    notifyListeners();
    try {
      _packageHistory[plan.id] = await repository.simulatePackage(plan);
    } catch (e) {
      showToast(
        'История пакета не посчиталась: ${_errorText(e)}',
        tone: ToastTone.failure,
      );
    } finally {
      _packageLoading.remove(plan.id);
      notifyListeners();
    }
  }

  /// Фактические веса пакета из книги и предложение по ребалансировке.
  ///
  /// Веса считаются по контурам: позиция принадлежит классу через контур,
  /// заданный при записи операции. Это не догадка по тикеру, а то, что
  /// владелец сам указал.
  RebalancePlan? rebalance(PackagePlan plan) {
    final state = _capital;
    if (state == null || state.isEmpty) return null;

    final byContour = <Contour, Money>{
      for (final slice in state.allocation()) slice.contour: slice.value,
    };
    final values = <AssetClass, Money>{};
    for (final target in plan.targets) {
      final contour = target.assetClass.contour;
      final share = plan.targets
          .where((t) => t.assetClass.contour == contour)
          .fold(0.0, (sum, t) => sum + t.weightPercent);
      final total = byContour[contour] ?? Money.zero(state.base);
      // Внутри контура делим пропорционально целевым весам: точнее книга
      // сейчас не знает, и придумывать разбивку по тикерам нельзя.
      values[target.assetClass] =
          share <= 0 ? Money.zero(state.base) : total.scaleBy(target.weightPercent / share);
    }

    return Rebalancer.plan(
      plan: plan,
      values: values,
      total: state.totalEquity,
    );
  }

  final Map<String, TargetAllocation> _allocations = {};

  /// Разбор пакета до инструментов: сколько чего в штуках и в деньгах.
  TargetAllocation? allocation(String id) => _allocations[id];

  /// Считает разбор пакета по живым ценам инструментов.
  ///
  /// Проценты в терминал не выставляются — владельцу нужен список «купить N
  /// штук такого-то на M рублей». Цены берутся с MOEX и Bybit; инструмент,
  /// по которому цены нет, не подменяется похожим.
  Future<void> loadAllocation(PackagePlan plan) async {
    if (_thinMode) {
      showToast(
        'Количество инструментов должен рассчитать сервер; прямые котировки '
        'с телефона в thin отключены',
        tone: ToastTone.warning,
      );
      return;
    }
    final repository = _repository;
    final state = _capital;
    if (repository is! LocalAnalysisRepository || state == null) return;
    if (!_packageLoading.add('alloc:${plan.id}')) return;
    notifyListeners();
    try {
      final quotes = await repository.packageQuotes(plan);
      final holdings = await repository.packageHoldings(plan);
      _allocations[plan.id] = TargetAllocation.of(
        plan: plan,
        total: state.totalEquity,
        quotes: quotes,
        holdings: holdings,
      );
    } catch (e) {
      showToast(
        'Цены инструментов не пришли: ${_errorText(e)}',
        tone: ToastTone.failure,
      );
    } finally {
      _packageLoading.remove('alloc:${plan.id}');
      notifyListeners();
    }
  }

  bool allocationLoading(String id) => _packageLoading.contains('alloc:$id');

  // ── Опционы ────────────────────────────────────────────────────────────

  /// Базовые активы, по которым приложение умеет собирать конструкции.
  ///
  /// Список короткий сознательно: это самые ликвидные серии FORTS. На
  /// неликвидном страйке можно войти и не выйти, и предлагать такие
  /// конструкции — медвежья услуга.
  static const optionAssets = ['SI', 'RI', 'BR', 'GOLD', 'CNY'];

  String _optionAsset = optionAssets.first;
  List<OptionContract> _optionChain = const [];
  List<OptionStructure> _structures = const [];
  double? _optionFuturesPrice;
  double? _optionVolatility;
  bool _optionsLoading = false;
  String? _optionsError;
  OptionsChainResult? _optionsProbe;

  String get optionAsset => _optionAsset;
  List<OptionContract> get optionChain => _optionChain;
  List<OptionStructure> get structures => _structures;
  double? get optionFuturesPrice => _optionFuturesPrice;
  double? get optionVolatility => _optionVolatility;
  bool get optionsLoading => _optionsLoading;
  String? get optionsError => _optionsError;

  /// Протокол последнего запроса цепочки: чем спрашивали и что пришло.
  OptionsChainResult? get optionsProbe => _optionsProbe;

  /// Почему цепочки нет — по протоколу, а не общими словами.
  ///
  /// «Цепочки нет» не говорит ничего и не даёт что делать дальше. Строка
  /// ниже говорит, каким путём спрашивали, сколько ушло запросов и что
  /// ответила биржа.
  String? get optionsDiagnosis {
    if (_optionChain.isNotEmpty) return null;
    final probe = _optionsProbe;
    if (probe == null) return null;
    final failure = probe.failure;
    if (failure != null) return 'биржа не ответила: $failure';
    final columns = probe.reports
        .expand((r) => r.blocks.entries)
        .where((e) => e.value.present)
        .map((e) => '${e.key}: ${e.value.columns.length} колонок')
        .toSet()
        .join(' · ');
    return 'путь «${probe.note}», запросов ${probe.requests}, '
        'строк ${probe.reports.fold<int>(0, (s, r) => s + r.rows)}'
        '${columns.isEmpty ? '' : ' · $columns'}';
  }

  /// Читает цепочку опционов и собирает конструкции с ограниченным риском.
  Future<void> loadOptionChain({String? asset}) async {
    if (_thinMode) {
      _optionsError = 'Опционные конструкции пока не выдаются сервером';
      notifyListeners();
      return;
    }
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) {
      _optionsError = 'Цепочка опционов доступна только в автономном расчёте';
      notifyListeners();
      return;
    }
    if (_optionsLoading) return;
    _optionAsset = (asset ?? _optionAsset).toUpperCase();
    _optionsLoading = true;
    _optionsError = null;
    notifyListeners();

    try {
      final (chain, probe) = await repository.optionChainProbe(_optionAsset);
      final futures = await repository.underlyingPrice(_optionAsset);
      _optionChain = chain;
      _optionsProbe = probe;
      _optionFuturesPrice = futures;

      if (chain.isEmpty || futures == null) {
        _structures = const [];
        _optionVolatility = null;
      } else {
        final now = DateTime.now();
        _optionVolatility =
            StructureBuilder.seriesVolatility(chain, futures, now) ?? 0.3;
        // Позиция в базовом активе решает, доступны ли покрытый колл и
        // защитный пут: без неё это голая продажа, которой здесь нет.
        final lots = _unprotected
                .where((p) => p.symbol.toUpperCase().startsWith(_optionAsset))
                .fold<double>(0, (sum, p) => sum + (p.long ? p.quantity : -p.quantity))
                .round();
        _structures = StructureBuilder.build(
          chain: chain,
          futuresPrice: futures,
          at: now,
          underlyingLots: lots > 0 ? lots : 0,
        );
      }
    } catch (e) {
      _optionsError = _errorText(e);
      _optionChain = const [];
      _structures = const [];
    } finally {
      _optionsLoading = false;
      notifyListeners();
    }
  }

  // ── Счета Т-Инвестиций ─────────────────────────────────────────────────

  List<TInvestAccount> _tinvestAccounts = const [];

  /// Счета, видимые токеном Т-Инвестиций.
  List<TInvestAccount> get tinvestAccounts => _tinvestAccounts;

  /// Счёт, с которого разрешено торговать. null — первый с полным доступом.
  String? get tinvestTradingAccount =>
      tradingDesk?.tradingState.tinvestAccountId;

  /// Спрашивает у брокера список счетов.
  Future<void> refreshTinvestAccounts() async {
    if (_thinMode) return;
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    _tinvestAccounts = await repository.tinvestAccounts();
    notifyListeners();
  }

  /// Назначает торговый счёт. Остальные остаются на чтение.
  /// Какие роли токенов Т-Инвестиций уже заведены.
  Set<TInvestRole> _tinvestTokens = const {};
  Set<TInvestRole> get tinvestTokens => _tinvestTokens;

  Future<void> refreshTinvestTokens() async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    _tinvestTokens = await repository.tinvestTokenRoles();
    notifyListeners();
  }

  /// Сохранить токен под его ролью.
  Future<void> saveTinvestToken(TInvestRole role, String token) async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    try {
      final answer = await repository.saveTinvestToken(role, token);
      showToast(answer);
    } on FeatureUnavailableException catch (e) {
      showError(e);
    }
    await refreshTinvestTokens();
    if (!_thinMode) await refreshTinvestAccounts();
  }

  /// Разрешить или запретить приложению читать счёт.
  ///
  /// Токен видит все счета владельца — ограничить его на стороне брокера
  /// нельзя. Значит ограничивает приложение, и делает это явным списком.
  Future<void> setTinvestAccountAccess(String accountId, bool allowed) async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    await repository.setTinvestAccountAccess(accountId, allowed);
    await refreshTinvestAccounts();
    notifyListeners();
  }

  /// Счета, разрешённые к чтению. Пустое множество — доступ не выдавали.
  Set<String> get tinvestAllowedAccounts =>
      tradingDesk?.tradingState.allowedAccountIds ?? const {};

  Future<void> setTinvestAccount(String? accountId) async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    await repository.setTinvestAccount(accountId);
    await _reloadSettings();
    showToast(accountId == null
        ? 'Торговый счёт: первый с полным доступом'
        : 'Торговый счёт назначен');
  }

  /// Журнал пересчётов дайджеста — от новых к старым.
  ///
  /// Настройка «раз в час» — это обещание, а журнал — факт. Владелец не мог
  /// отличить «пересчиталось и ничего не нашлось» от «не пересчитывалось».
  List<DigestRun> get digestRuns {
    final repository = _repository;
    return repository is LocalAnalysisRepository ? repository.digestRuns : const [];
  }

  /// Сводка по прогонам за сутки для строки в здоровье данных.
  String get digestRunsNote {
    final runs = digestRuns;
    if (runs.isEmpty) return 'пересчётов ещё не было';
    final since = DateTime.now().subtract(const Duration(hours: 24));
    final day = runs.where((r) => r.at.isAfter(since)).toList();
    final failed = day.where((r) => r.failed).length;
    final background = day.where((r) => !r.foreground).length;
    final last = runs.first.at.toLocal();
    final stamp = '${last.hour.toString().padLeft(2, '0')}:'
        '${last.minute.toString().padLeft(2, '0')}';
    return 'пересчётов за сутки: ${day.length}'
        '${background > 0 ? ' (в фоне $background)' : ''}'
        '${failed > 0 ? ', с ошибкой $failed' : ''} · последний $stamp';
  }

  // ── Площадки ───────────────────────────────────────────────────────────

  List<VenueStatus> _venues = const [];

  /// Состояние каждой площадки: режим, ключи, чем читаем и почему молчит.
  ///
  /// Экран счетов обязан перечислять все площадки, а не только те, что
  /// ответили. Площадка без ключей — это строка с причиной; пустота на её
  /// месте выглядит как «Bybit не существует», и владелец справедливо
  /// считает, что приложение врёт.
  List<VenueStatus> get venues => _venues;

  Future<void> refreshVenues() async {
    if (_thinMode) return;
    final desk = tradingDesk;
    if (desk == null) return;
    final result = <VenueStatus>[];
    for (final id in BrokerId.values) {
      final modes = await desk.brokerKeyModes(id);
      final readable = await desk.readableMode(id);
      final current = desk.tradingState.modeOf(id);
      result.add(VenueStatus(
        id: id,
        mode: current,
        keyModes: modes,
        readable: readable,
        check: desk is TradingProbe ? (desk as TradingProbe).keyCheckOf(id) : null,
      ));
    }
    _venues = result;
    notifyListeners();
  }

  /// Здоровье данных для чипа в шапке.
  ///
  /// Признаки деградации приложение считало давно — `lastResultPartial`,
  /// оговорка в `sourceNote`, журнал прогонов, — но не показывало нигде.
  /// Экран выглядел одинаково и когда всё посчитано, и когда половина
  /// источников молчала: владелец видел пустую ленту и решал, что приложение
  /// мертво.
  ///
  /// Через `RiskMode.caution` это выражать нельзя: он означает «допуск к
  /// живым деньгам урезан» — про риск, а не про источники.
  DataHealth get dataHealth {
    if (_thinMode) {
      if (!_engineIdeas.isAvailable) return DataHealth.blind;
      final status = _engineDataStatus;
      if (status == null) return DataHealth.partial;
      final tradable = (status['tradable'] as num?)?.toInt() ?? 0;
      final withData = (status['with_data'] as num?)?.toInt() ?? 0;
      final quality =
          status['recent_quality_events'] as List<dynamic>? ?? const [];
      return withData < tradable || quality.isNotEmpty
          ? DataHealth.partial
          : DataHealth.full;
    }
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return DataHealth.full;
    if (_digest == null || repository.lastRefreshError != null) {
      return DataHealth.blind;
    }
    return repository.lastResultPartial ? DataHealth.partial : DataHealth.full;
  }

  /// Чем именно болеют данные — строкой для подсказки под чипом.
  ///
  /// Собирается из того, что уже посчитано: оговорка расчёта и журнал
  /// прогонов. Без неё чип сообщает диагноз, но не причину, а «неполные»
  /// без «чего именно не хватило» — та же отписка, что и молчание.
  String get dataHealthDetail {
    if (_thinMode) {
      final unavailable = _engineIdeas.unavailableReason;
      if (unavailable != null) return unavailable;
      final status = _engineDataStatus;
      if (status == null) {
        return 'Идеи получены, но сервер не отдал состояние рыночных данных.';
      }
      final tradable = (status['tradable'] as num?)?.toInt() ?? 0;
      final withData = (status['with_data'] as num?)?.toInt() ?? 0;
      return 'Сервер: данные есть у $withData из $tradable торгуемых '
          'инструментов.';
    }
    final repository = _repository;
    final parts = <String>[];
    if (repository is LocalAnalysisRepository) {
      final failure = repository.lastRefreshError;
      if (failure != null) {
        parts.add(failure is MarketDataException
            ? failure.message
            : 'последний пересчёт не удался');
      }
    }
    final note = _digest?.sourceNote ?? '';
    final marker = note.indexOf('Внимание: расчёт неполный —');
    if (marker >= 0) {
      parts.add(note.substring(marker).replaceAll('\n', ' ').trim());
    }
    parts.add(digestRunsNote);
    return parts.join(' · ');
  }

  /// Подпись о свежести данных для шапки раздела.
  ///
  /// Число без времени, к которому оно относится, ничего не стоит: котировки
  /// могли встать полчаса назад, и владелец должен видеть это без перехода в
  /// диагностику.
  String? get dataFreshness {
    final at = _thinMode ? _ideasFetchedAt : _digestFetchedAt;
    if (at == null) return null;
    final local = at.toLocal();
    final minutes = DateTime.now().difference(at).inMinutes;
    final stamp = '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    if (minutes < 2) return 'данные $stamp · только что';
    if (minutes < 60) return 'данные $stamp · $minutes мин назад';
    return 'данные $stamp · ${minutes ~/ 60} ч назад';
  }

  /// Аварийная остановка одним движением: кнопка рейла.
  ///
  /// Два тапа максимум (ТЗ §15): переключение и подтверждение тостом. Снятие
  /// — тем же переключателем, но осознанно: включённый «Стоп» виден всегда.
  Future<void> toggleKillSwitch() async {
    final desk = tradingDesk;
    if (desk == null) return;
    final on = desk.tradingState.killSwitch;
    final note = await desk.setKillSwitch(!on);
    showToast(note);
    notifyListeners();
  }

  /// Включена ли аварийная остановка.
  bool get killSwitchOn => tradingDesk?.tradingState.killSwitch ?? false;

  /// Уходят ли заявки на настоящую биржу.
  ///
  /// От этого зависит, что означают проверки маржи и защитных заявок: в
  /// бумажном режиме резервировать нечего и стоп ставить негде, а выдавать
  /// отсутствие брокера за отказ проверки нельзя — заблокировало бы всё.
  bool get liveTradingOn {
    final state = tradingDesk?.tradingState;
    return state != null && state.canSendOrders && state.anyLive;
  }

  /// Режим риск-движка по фактическому состоянию контура.
  ///
  /// Режим не выбирается руками (кроме аварийной остановки): его назначает
  /// состояние — иначе индикатор показывал бы намерение, а не факт.
  RiskMode get riskMode {
    final desk = tradingDesk;
    if (desk == null) return RiskMode.normal;
    final state = desk.tradingState;
    if (state.killSwitch) return RiskMode.killSwitch;
    if (!state.enabled) return RiskMode.reduceOnly;
    if (!desk.liveGate.allowed) return RiskMode.caution;
    return RiskMode.normal;
  }

  /// Очередь решений: что сегодня требует человека.
  ///
  /// Собирается из фактического состояния, а не из списка «полезных
  /// напоминаний»: идея живёт, пока не подтверждена; расхождение книги
  /// висит, пока не сведено; позиция без стопа — дефект и стоит первой.
  List<Decision> get decisions {
    final result = <Decision>[];

    for (final signal in _digest?.signals ?? const <TradingSignal>[]) {
      if (!signal.status.canConfirm) continue;
      result.add(Decision(
        kind: DecisionKind.idea,
        title: '${signal.symbol} · ${signal.direction.label} ${signal.score}/100',
        context: '${signal.name} · вход ${signal.lastPrice} · R:R ${signal.riskReward}',
        urgency: DecisionUrgency.today,
        target: signal.id,
      ));
    }

    final state = _capital;
    if (state != null) {
      if (state.snapshot.mismatches > 0) {
        result.insert(
          0,
          Decision(
            kind: DecisionKind.reconcile,
            title: 'Книга не сходится с брокером',
            context: 'записей с расхождением: ${state.snapshot.mismatches} — '
                'сверить до новых сделок',
            urgency: DecisionUrgency.now,
          ),
        );
      }
      if (!state.persistent && !state.isEmpty) {
        result.insert(
          0,
          const Decision(
            kind: DecisionKind.reconcile,
            title: 'Книга не пишется на диск',
            context: 'операции этой сессии не переживут перезапуск',
            urgency: DecisionUrgency.now,
          ),
        );
      }
    }

    for (final position in _unprotected) {
      result.insert(
        0,
        Decision(
          kind: DecisionKind.unprotected,
          title: '${position.symbol} без стопа',
          context: 'позиция открыта, защита на бирже не стоит',
          urgency: DecisionUrgency.now,
          target: position.symbol,
        ),
      );
    }

    return result;
  }

  List<BrokerPosition> _unprotected = const [];

  /// Обновляет список незащищённых позиций для очереди решений.
  Future<void> refreshUnprotected() async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    try {
      _unprotected = await repository.unprotectedPositions();
      notifyListeners();
    } on Exception {
      // Площадка молчит — очередь просто не покажет этот пункт, а не соврёт.
    }
  }

  // ── Раздел «Инвест» ────────────────────────────────────────────────────

  Future<void> refreshInvest({bool force = false}) async {
    if (_thinMode) return;
    final desk = investDesk;
    if (desk == null || _investLoading) return;
    _investLoading = true;
    _investError = null;
    final repository = _repository;
    if (repository is ProgressReporting) {
      (repository as ProgressReporting).onProgress = (stage) {
        _analysisStage = stage;
        notifyListeners();
      };
    }
    notifyListeners();
    try {
      _invest = await desk.fetchInvestDigest(force: force);
    } catch (e) {
      _investError = e;
    } finally {
      _investLoading = false;
      _analysisStage = null;
      if (repository is ProgressReporting) {
        (repository as ProgressReporting).onProgress = null;
      }
      notifyListeners();
    }
  }

  Future<void> runInvestBacktest() async {
    if (_thinMode) {
      showToast(
        'В thin-режиме локальный бэктест отключён',
        tone: ToastTone.warning,
      );
      return;
    }
    final desk = investDesk;
    if (desk == null || _investBacktestRunning) return;
    _investBacktestRunning = true;
    final repository = _repository;
    if (repository is ProgressReporting) {
      (repository as ProgressReporting).onProgress = (stage) {
        _analysisStage = stage;
        notifyListeners();
      };
    }
    notifyListeners();
    try {
      final result = await desk.runInvestBacktest();
      showToast('Бэктест акций завершён · PF ${_profitFactor(result)}');
    } catch (e) {
      showError(e);
    } finally {
      _investBacktestRunning = false;
      _analysisStage = null;
      if (repository is ProgressReporting) {
        (repository as ProgressReporting).onProgress = null;
      }
      notifyListeners();
    }
  }

  Future<void> optimizeInvest() async {
    if (_thinMode) {
      showToast(
        'В thin-режиме локальная оптимизация отключена',
        tone: ToastTone.warning,
      );
      return;
    }
    final desk = investDesk;
    if (desk == null || _investBacktestRunning) return;
    _investBacktestRunning = true;
    notifyListeners();
    try {
      showToast(await desk.optimizeInvestParameters());
    } catch (e) {
      showError(e);
    } finally {
      _investBacktestRunning = false;
      notifyListeners();
    }
  }

  void openSignal(String id) {
    _selectedSignalId = id;
    notifyListeners();
    // Лента приходит сводками: `/ideas/today` не несёт ни плана, ни
    // доказательств, ни разметки — они живут в `/ideas/{id}`. Пока полная
    // карточка не запрашивалась, разбор показывал «плана нет» и голый график
    // у идей, у которых на сервере есть и то и другое.
    unawaited(_hydrateIdea(id));
  }

  /// Догрузить полную карточку идеи и заменить ею сводку в ленте.
  Future<void> _hydrateIdea(String id) async {
    if (demoData) return;
    final current = _engineIdeas.ideas.where((i) => i.id == id).firstOrNull;
    // Уже полная — доказательства бывают только в детальном ответе.
    if (current != null && current.evidence.isNotEmpty) return;
    var full = await _engine.detail(id);
    // `/ideas/today` намеренно не несёт TradePlan. Один краткий сбой
    // detail не должен превращать хороший серверный сигнал в карточку без
    // Entry/SL/TP и кнопки подтверждения до следующего открытия экрана.
    // Повторяем только чтение уже созданного плана; генерацию сигнала,
    // триггер и торговые уровни здесь не пересчитываем.
    full ??= await _engine.detail(id);
    if (full == null) {
      if (_selectedSignalId == id) {
        showToast(
          'Не удалось загрузить торговый план. Закройте и откройте идею, '
          'чтобы повторить.',
          tone: ToastTone.warning,
        );
      }
      return;
    }
    // Корзина `/ideas/today` — явный ответ сервера на вопрос «можно ли
    // действовать сейчас». Detail старых версий этого поля не несёт и может
    // содержать тот же lifecycle/quality status для обеих корзин. Поэтому
    // догрузка плана не вправе превратить `wait_for_trigger` в actionable.
    final hydrated = current == null
        ? full
        : full.copyWith(
            readiness: current.readiness,
            actionable: current.actionable,
          );
    // Идеи может не быть в ленте вовсе: терминальная ушла из выдачи, а
    // бумажная сделка по ней жива и ссылается сюда из журнала. Раньше
    // здесь стоял выход — и разбор такой идеи оставался пустым навсегда:
    // «сделка есть, идей по ней нет». Сервер деталь терминальной идеи
    // хранит и отдаёт; добавляем её в ленту, а не только заменяем.
    _engineIdeas = EngineIdeas(
      ideas: [
        if (current == null) hydrated,
        for (final idea in _engineIdeas.ideas)
          idea.id == id ? hydrated : idea,
      ],
      unavailableReason: _engineIdeas.unavailableReason,
      noSetupsReason: _engineIdeas.noSetupsReason,
    );
    notifyListeners();
  }

  void back() {
    _selectedSignalId = null;
    _sheetOpen = false;
    notifyListeners();
  }

  void openSheet() {
    _sheetOpen = true;
    notifyListeners();
  }

  void closeSheet() {
    _sheetOpen = false;
    notifyListeners();
  }

  // ── Действия ───────────────────────────────────────────────────────────

  /// Подтверждение сделки. Сервер выставляет лимитку и OCO (SL + TP).
  ///
  /// Просроченный или инвалидированный сигнал подтвердить нельзя (ТЗ §1).
  Future<void> confirmCurrentSignal() async {
    final signal = currentSignal;
    if (signal == null || !signal.status.canConfirm || _confirming) return;

    final idea = ideas.where((i) => i.id == signal.id).firstOrNull;
    final serverIdea = !demoData && idea != null;
    // Для серверной идеи одного программного вызова недостаточно: владелец
    // обязан сначала открыть sheet с полным paper-планом. Биометрия ниже —
    // дополнительный рубеж, а не замена явному подтверждению на экране.
    if (serverIdea && (!_sheetOpen || !idea.canApprovePaper)) return;

    _confirming = true;
    notifyListeners();

    // Серверная идея создаёт только серверную paper-сделку. Раньше тот же
    // тап шёл в LocalAnalysisRepository, открывал параллельную позицию на
    // телефоне и мог даже отправить брокерскую заявку — при том что экран
    // показывал план другого, серверного контура.
    if (serverIdea) {
      final server = idea;
      try {
        if (!await _confirmOnDeviceIfAvailable(server)) {
          _sheetOpen = false;
          showToast('Подтверждение отменено', tone: ToastTone.warning);
          return;
        }
        final decision = await _engine.approvePaper(server.id);
        final trade = decision.trade;
        if (trade != null) _upsertServerPaperTrade(trade);
        // Ответ approve уже достаточен для мгновенного экрана, а перечитка
        // синхронизирует состояние после идемпотентного replay и серверного
        // сопровождения.
        final trades = await _engine.paperTrades();
        if (trades != null) _serverPaperTrades = trades;
        _engineIdeas = EngineIdeas(
          ideas: [
            for (final item in _engineIdeas.ideas)
              item.id == server.id
                  ? item.copyWith(
                      state: IdeaState.active,
                      actionable: false,
                    )
                  : item,
          ],
          unavailableReason: _engineIdeas.unavailableReason,
          noSetupsReason: _engineIdeas.noSetupsReason,
        );
        _sheetOpen = false;
        showToast(
          decision.idempotentReplay
              ? 'Paper-сделка уже принята · сопровождение на сервере'
              : 'Paper-сделка принята · сопровождение на сервере',
        );
      } catch (e) {
        _sheetOpen = false;
        showError(e);
      } finally {
        _confirming = false;
        notifyListeners();
      }
      return;
    }

    // Исполнение ведётся машиной состояний ТЗ §11.3, а не одним вызовом
    // брокера. Между показом плана и нажатием кнопки проходит время: цена
    // уходит, срок истекает, лимит выбирается соседней сделкой. Поэтому
    // предпроверка считается заново, а не берётся с экрана.
    var execution = Execution(
      ideaId: signal.id,
      planHash: idea?.plan?.hash ?? '',
      plannedQuantity: idea?.plan?.quantity ?? 0,
      state: ExecutionState.pendingConfirmation,
    );

    try {
      execution = execution.moveTo(ExecutionState.precheck);
      await _saveExecution(execution);

      final blockers = idea == null
          ? const <CheckResult>[]
          : FinalCheck.blockers(_finalCheck(idea));
      if (blockers.isNotEmpty) {
        // Отказ до денег: позиции нет, сверять нечего — закрываем.
        execution = execution.moveTo(
          ExecutionState.closed,
          note: 'предпроверка не пропустила: ${blockers.first.detail}',
        );
        await _saveExecution(execution);
        _sheetOpen = false;
        showToast(
          'Проверка не пропустила: ${blockers.first.kind.label}',
          tone: ToastTone.failure,
        );
        return;
      }

      execution = execution.moveTo(ExecutionState.submitEntry);
      await _saveExecution(execution);

      await _repository.confirmSignal(signal.id);
      _applySignalStatus(signal.id, SignalStatus.working);
      _sheetOpen = false;
      showToast('Ордер отправлен · OCO SL + TP выставлены');
      // Заявка — ещё не позиция. В книгу сделка попадёт исполнением, которое
      // придёт в выписке брокера, поэтому здесь запускается сверка, а не
      // запись «на всякий случай»: капитал не должен меняться от намерения.
      unawaited(refreshCapital(sync: true));
    } catch (e) {
      // Ошибка на пути к бирже — безопасное состояние и сверка, а не тихий
      // тост: заявка могла уйти, и знать об этом важнее, чем закрыть шит.
      await _saveExecution(execution.toSafeState(_errorText(e)));
      _sheetOpen = false;
      showError(e);
    } finally {
      _confirming = false;
      notifyListeners();
    }
  }

  /// Системное подтверждение — дополнительный рубеж после явного sheet.
  ///
  /// Если на устройстве настроен отпечаток/лицо/PIN, используем его. На
  /// устройстве без системной защиты основной paper-flow не блокируется:
  /// обязательное явное подтверждение уже произошло в экранном sheet.
  Future<bool> _confirmOnDeviceIfAvailable(Idea idea) async {
    final method = await _bridge.confirmMethod();
    if (method == null || method == 'none') return true;
    return _bridge.biometricConfirm(
      title: '${idea.direction.isLong ? 'Paper-покупка' : 'Paper-продажа'} '
          '${idea.symbolOrId}',
      subtitle: 'После подтверждения сделку сопровождает сервер',
    );
  }

  void _upsertServerPaperTrade(PaperPosition trade) {
    _serverPaperTrades = [
      trade,
      for (final item in _serverPaperTrades)
        if (item.id != trade.id && item.ideaId != trade.ideaId) item,
    ];
  }

  /// Финальная проверка идеи в текущих условиях (ТЗ §11.1).
  List<CheckResult> _finalCheck(Idea idea) {
    final center = riskCenter;
    final plan = idea.plan;
    if (center == null || plan == null) return const [];
    return FinalCheck.run(
      idea,
      ExecutionContext(
        now: DateTime.now(),
        lastPrice: _parsePrice(currentSignal?.lastPrice),
        budget: center.budgetFor(idea.score),
        freeMargin: null,
        clusterRiskAfterPercent:
            center.clusterRisk.usedPercent + plan.riskPercent,
        shownPlanHash: plan.hash,
        paperMode: !liveTradingOn,
      ),
    );
  }

  static double? _parsePrice(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    return double.tryParse(raw.replaceAll(' ', '').replaceAll(',', '.'));
  }

  /// Исполнение по идее. null — подтверждения ещё не было.
  Execution? execution(String ideaId) {
    if (_thinMode) return _memoryExecutions[ideaId];
    final repository = _repository;
    return repository is LocalAnalysisRepository
        ? repository.executions[ideaId]
        : _memoryExecutions[ideaId];
  }

  /// Исполнения, которые ещё не завершены.
  List<Execution> get liveExecutions {
    if (_thinMode) {
      return [
        for (final execution in _memoryExecutions.values)
          if (!execution.state.isTerminal) execution,
      ];
    }
    final repository = _repository;
    final all = repository is LocalAnalysisRepository
        ? repository.executions.values
        : _memoryExecutions.values;
    return [for (final e in all) if (!e.state.isTerminal) e];
  }

  /// Исполнения режима без диска: приложение обязано вести состояние даже
  /// там, где его некуда записать, иначе экран врёт после первого же шага.
  final Map<String, Execution> _memoryExecutions = {};

  Future<void> _saveExecution(Execution execution) async {
    final repository = _repository;
    if (repository is LocalAnalysisRepository) {
      await repository.saveExecution(execution);
    } else {
      _memoryExecutions[execution.ideaId] = execution;
    }
    notifyListeners();
  }

  // ── Бумажный журнал ────────────────────────────────────────────────────

  /// Журнал бумажных сделок. null — режим без журнала (демо, сервер).
  PaperTracking? get _paper {
    final repository = _repository;
    return repository is PaperTracking ? repository as PaperTracking : null;
  }

  /// Как идея уже ведётся на бумаге. null — не ведётся или журнала нет.
  String? paperNote(TradingSignal signal) => _paper?.paperNoteFor(signal.symbol);

  /// Есть ли вообще бумажный журнал: без него кнопку показывать незачем.
  bool get paperAvailable => _paper != null;

  /// Завести текущую идею на бумаге.
  ///
  /// Отдельно от подтверждения сделки намеренно: бумажный журнал не зависит
  /// ни от ключей, ни от биржи, ни от подтверждения — и запретить его тем,
  /// что торговля выключена, было бы бессмысленно.
  Future<void> trackCurrentSignalOnPaper() async {
    final signal = currentSignal;
    final paper = _paper;
    if (signal == null || paper == null) return;
    if (!demoData &&
        _engineIdeas.ideas.any((idea) => idea.id == signal.id)) {
      // Серверная идея может жить только в server paper ledger. Две позиции
      // по одному плану расходятся по стопам и тейкам уже на первом баре.
      showToast(
        'Серверная идея ведётся только после подтверждения paper-сделки',
        tone: ToastTone.warning,
      );
      return;
    }
    try {
      // Сигнал передаётся целиком, а не идентификатором: идея с движка в
      // выдаче дайджеста не лежит, и поиск по идентификатору отвечал бы
      // «идея больше не в выдаче» на идею, открытую прямо сейчас.
      showToast(await paper.trackSignalOnPaper(signal));
    } catch (e) {
      showError(e);
    }
    notifyListeners();
    // Журнал изменился — вкладка «Сделки» должна показывать это сразу, а не
    // после следующего пересчёта.
    try {
      _trades = await _repository.fetchTrades();
      notifyListeners();
    } on Object {
      // Не удалось перечитать — запись всё равно сделана.
    }
  }

  Future<void> toggleStrategy(String id, bool enabled) async {
    final snapshot = _strategies;
    if (snapshot == null) return;
    _strategies = snapshot.copyWith(
      packs: [
        for (final p in snapshot.packs) p.id == id ? p.copyWith(enabled: enabled) : p,
      ],
    );
    notifyListeners();
    try {
      await _repository.setStrategyEnabled(id, enabled);
    } catch (e) {
      _strategies = snapshot; // откат оптимистичного переключения
      showError(e);
      notifyListeners();
    }
  }

  Future<void> runBacktest(String strategyId) async {
    if (_thinMode) {
      showToast(
        'В thin-режиме локальный бэктест отключён',
        tone: ToastTone.warning,
      );
      return;
    }
    if (_backtestRunning) return;
    _backtestRunning = true;
    final repository = _repository;
    if (repository is ProgressReporting) {
      (repository as ProgressReporting).onProgress = (stage) {
        _analysisStage = stage;
        notifyListeners();
      };
    }
    notifyListeners();
    try {
      final result = await _repository.runBacktest(strategyId);
      _strategies = _strategies?.copyWith(backtest: result);
      showToast('Бэктест завершён · PF ${_profitFactor(result)}');
    } catch (e) {
      showError(e);
    } finally {
      _backtestRunning = false;
      _analysisStage = null;
      if (repository is ProgressReporting) {
        (repository as ProgressReporting).onProgress = null;
      }
      notifyListeners();
    }
  }

  Future<void> connectExchange(String id) async {
    final snapshot = _settings;
    if (snapshot == null) return;
    try {
      final updated = await _repository.connectExchange(id);
      _settings = snapshot.copyWith(
        exchanges: [
          for (final e in snapshot.exchanges) e.id == id ? updated : e,
        ],
      );
      showToast('${updated.name} подключена по API-ключу');
    } catch (e) {
      showError(e);
    }
    notifyListeners();
  }

  // ── Торговый контур ─────────────────────────────────────────────────────

  /// Торговый доступ репозитория. null — режим без исполнения сделок.
  TradingDesk? get _desk {
    // Явное приведение, а не promotion: TradingDesk не наследник
    // SignalAiRepository — это независимая способность реализации.
    final repository = _repository;
    return repository is TradingDesk ? repository as TradingDesk : null;
  }

  /// Торговый контур для экранов, которым нужен он сам, а не срез состояния:
  /// диагностика гоняет живые запросы к биржам.
  TradingDesk? get tradingDesk => _thinMode ? null : _desk;

  /// Сохраняет ключи биржи и сразу проверяет их: молча принять нерабочий ключ
  /// значит узнать об этом в момент отправки ордера.
  Future<void> saveBrokerKeys(BrokerId broker, String apiKey, String apiSecret) async {
    if (_thinMode) return;
    final desk = _desk;
    if (desk == null) return;
    try {
      final answer = await desk.saveBrokerKeys(
        broker: broker,
        mode: desk.tradingState.modeOf(broker),
        apiKey: apiKey,
        apiSecret: apiSecret,
      );
      showToast(answer);
    } catch (e) {
      showError(e);
    }
    await _reloadSettings();
  }

  Future<void> setTradingEnabled(bool enabled) async {
    if (_thinMode) return;
    final desk = _desk;
    if (desk == null) return;
    await desk.setTradingEnabled(enabled);
    showToast(enabled
        ? 'Отправка ордеров включена — каждая сделка всё равно подтверждается'
        : 'Отправка ордеров выключена');
    await _reloadSettings();
  }

  Future<void> setTradingMode(BrokerId broker, TradingMode mode) async {
    if (_thinMode) return;
    final desk = _desk;
    if (desk == null) return;
    try {
      await desk.setTradingMode(broker, mode);
      showToast('${broker.title}: ${mode.labelFor(broker)}');
    } catch (e) {
      showError(e);
    }
    await _reloadSettings();
  }

  Future<void> setKillSwitch(bool on) async {
    if (_thinMode) return;
    final desk = _desk;
    if (desk == null) return;
    showToast(await desk.setKillSwitch(on));
    await _reloadSettings();
  }

  Future<void> _reloadSettings() async {
    try {
      _settings = await _repository.fetchSettings();
    } catch (_) {
      // Снимок не обновился — на экране останется прежний.
    }
    notifyListeners();
  }

  // ── Фоновый контур ──────────────────────────────────────────────────────

  Future<void> setBackgroundEnabled(bool enabled) async {
    final desk = _desk;
    if (desk == null) return;
    await desk.setBackgroundEnabled(enabled);
    if (enabled) {
      showToast('Фоновый контур включён: ${desk.backgroundMode.label}');
    } else {
      await _bridge.monitorStop();
      showToast('Фоновый контур выключен');
    }
    await _reloadSettings();
  }

  Future<void> setBackgroundMode(BackgroundMode mode) async {
    final desk = _desk;
    if (desk == null) return;
    await desk.setBackgroundMode(mode);
    // Работающий контур перезапускаем: режим читается при старте сервиса.
    if (desk.backgroundEnabled && await _bridge.monitorRunning()) {
      await _bridge.monitorStop();
      await _bridge.monitorStart(mode.name);
    }
    showToast('Фон: ${mode.label}');
    await _reloadSettings();
  }

  /// Уход в фон: отдаём владение состоянием и поднимаем контур.
  Future<void> onAppPaused() async {
    final desk = _desk;
    await _lock?.release(StateLock.ui);
    if (desk == null || !desk.backgroundEnabled) return;
    await _bridge.monitorStart(desk.backgroundMode.name);
  }

  /// Возврат на передний план: считает снова интерфейс.
  Future<void> onAppResumed() async {
    // Thin poll пишет отдельный server snapshot и может безопасно закончить
    // параллельно UI. Не вызываем monitorStop: он не только снимает текущий
    // service, но и удаляет будильник/boot-флаг, поэтому после обычного
    // открытия приложения фон забывался до следующего pause.
    if (!_thinMode) await _bridge.monitorStop();
    await _lock?.heartbeat(StateLock.ui);
    // Нажатие по уведомлению у работающего приложения приходит сюда: система
    // не перезапускает его, а возвращает на передний план.
    await openFromNotification();
    // Thin-фон меняет только серверный snapshot. Foreground перечитывает тот
    // же источник истины; legacy/dev по-прежнему обновляет свой дайджест.
    if (_thinMode) {
      await refreshIdeas();
    } else {
      await refreshDigest();
    }
    // Книга могла измениться извне (импорт, фоновая сверка) — и капитал
    // показывался бы устаревшим до первого перехода между разделами.
    await refreshCapital();
  }

  Future<void> toggleChannel(String id, bool enabled) async {
    final snapshot = _settings;
    if (snapshot == null) return;
    // Включение пуша — момент спросить разрешение системы (Android 13+).
    if (id == 'push' && enabled) {
      final granted = await _bridge.requestNotificationPermission();
      if (!granted) {
        // Диалог мог и не показаться: после двух отказов Android молчит.
        // Тогда единственный путь — системный экран уведомлений приложения,
        // и мы открываем его сами, а не отправляем искать.
        showToast('Включите уведомления SignalAI на открывшемся экране '
            'и вернитесь в приложение');
        await _bridge.openNotificationSettings();
      }
    }
    _settings = snapshot.copyWith(
      channels: [
        for (final c in snapshot.channels) c.id == id ? c.copyWith(enabled: enabled) : c,
      ],
    );
    notifyListeners();
    try {
      await _repository.setChannelEnabled(id, enabled);
    } catch (e) {
      _settings = snapshot;
      showError(e);
      notifyListeners();
    }
  }

  /// Тестовый пуш с примером идеи — проверить доставку, не дожидаясь сигнала.
  ///
  /// Пример помечен явно: уведомление, которое можно перепутать с реальной
  /// идеей, опаснее отсутствия уведомлений.
  Future<void> sendTestPush() async {
    final granted = await _bridge.requestNotificationPermission();
    if (!granted) {
      showToast('Уведомления запрещены системой — включите их на '
          'открывшемся экране и повторите');
      await _bridge.openNotificationSettings();
      return;
    }
    final delivered = await _bridge.notify(
      id: 990,
      title: 'ТЕСТ · BTCUSDT · Лонг · 87/100',
      body: 'Так выглядит пуш о новой идее: вход 60 120 · SL 58 940 · '
          'R:R 2,2. Это пример, не сигнал.',
    );
    showToast(delivered
        ? 'Тестовый пуш отправлен — смотрите шторку уведомлений'
        : 'Система не показала уведомление: проверьте разрешение и '
            'режим «Не беспокоить»');
  }

  Future<void> toggleNotification(String id, bool enabled) async {
    final snapshot = _settings;
    if (snapshot == null) return;
    _settings = snapshot.copyWith(
      notifications: [
        for (final n in snapshot.notifications) n.id == id ? n.copyWith(enabled: enabled) : n,
      ],
    );
    notifyListeners();
    try {
      await _repository.setNotificationEnabled(id, enabled);
    } catch (e) {
      _settings = snapshot;
      showError(e);
      notifyListeners();
    }
  }

  /// Обновление политики риска — объёмы позиций пересчитываются во всём
  /// приложении, как в макете.
  Future<void> updateRisk({double? deposit, double? riskPercent}) async {
    final snapshot = _settings;
    if (snapshot == null) return;
    _settings = snapshot.copyWith(
      risk: snapshot.risk.copyWith(deposit: deposit, riskPercent: riskPercent),
    );
    notifyListeners();
    try {
      final updated = await _repository.updateRiskProfile(
        deposit: deposit,
        riskPercent: riskPercent,
      );
      _settings = _settings?.copyWith(risk: updated);
      // Параметры стратегии показывают риск на сделку — перечитываем.
      _strategies = await _repository.fetchStrategies();
    } catch (e) {
      _settings = snapshot;
      showError(e);
    }
    notifyListeners();
  }

  // ── Тост ───────────────────────────────────────────────────────────────

  void showToast(String message, {ToastTone tone = ToastTone.success}) {
    _toast = message;
    _toastTone = tone;
    notifyListeners();
    _toastTimer?.cancel();
    _toastTimer = Timer(const Duration(milliseconds: 2600), () {
      _toast = null;
      notifyListeners();
    });
  }

  void _applySignalStatus(String id, SignalStatus status) {
    final digest = _digest;
    if (digest == null) return;
    _digest = digest.copyWith(
      signals: [
        for (final s in digest.signals) s.id == id ? s.copyWith(status: status) : s,
      ],
    );
  }

  String _profitFactor(BacktestResult result) {
    for (final stat in result.stats) {
      if (stat.label.contains('профит-фактор')) return stat.value;
    }
    return '—';
  }

  /// Текст ошибки для пользователя. «Нет связи с сервером» здесь возможен
  /// только для серверного режима: в автономном сервера нет, и сваливать на
  /// него честнее всего не получится.
  /// Показать отказ. Тон обязателен, поэтому он не параметр, а сам метод.
  ///
  /// Ручная пометка каждого места вызова не работает: их дюжина, и первый
  /// же новый отказ, добавленный не глядя, снова выйдет зелёной галочкой.
  /// Здесь тон задан происхождением текста — он приходит из исключения.
  void showError(Object e) =>
      showToast(_errorText(e), tone: ToastTone.failure);

  String _errorText(Object e) => switch (e) {
        ApiException(:final message) => message,
        FeatureUnavailableException(:final message) => message,
        UnimplementedError(:final String message) => message,
        _ => 'Не удалось получить данные: нет сети или биржа не отвечает. '
            'Попробуйте ещё раз.',
      };

  @override
  void dispose() {
    _toastTimer?.cancel();
    _autoRefreshTimer?.cancel();
    super.dispose();
  }
}
