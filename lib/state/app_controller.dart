import 'dart:async';

import 'package:flutter/foundation.dart';

import '../data/api/api_client.dart';
import '../data/broker/tinvest_broker.dart';
import '../data/local_analysis_repository.dart';
import '../data/native_bridge.dart';
import '../data/ledger/capital_desk.dart';
import '../data/repository.dart';
import '../domain/ledger/account.dart';
import '../domain/ledger/ledger_event.dart';
import '../domain/ledger/money.dart';
import '../domain/risk/portfolio_impact.dart';
import '../domain/risk/risk_engine.dart';
import 'navigation.dart';
import '../domain/broker/broker.dart';
import '../domain/enums.dart';
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

/// Состояние приложения: что показываем и что сейчас делает пользователь.
///
/// Никакой торговой логики здесь нет — только загрузка данных, навигация и
/// оптимистичные переключатели. Решения принимает сервер (ТЗ §2).
class AppController extends ChangeNotifier {
  AppController(this._repository, {NativeBridge bridge = const NativeBridge()})
      : _bridge = bridge {
    // Часовой пульс: пока приложение живо, идеи не старше часа. Проверка
    // раз в минуту, пересчёт — когда дайджест реально устарел.
    _autoRefreshTimer = Timer.periodic(const Duration(minutes: 1), (_) {
      // Отметка владения состоянием: пока приложение на переднем плане, оно
      // единственный писатель, и фоновый контур в это время не считает.
      _lock?.heartbeat(StateLock.ui);
      _autoRefreshIfStale();
    });
    _lock?.heartbeat(StateLock.ui);
  }

  final SignalAiRepository _repository;
  final NativeBridge _bridge;

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
  Timer? _toastTimer;
  bool _backtestRunning = false;
  bool _confirming = false;

  DailyDigest? _digest;
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
    final signals = _digest?.signals;
    if (signals == null || signals.isEmpty) return null;
    return signals.firstWhere(
      (s) => s.id == _selectedSignalId,
      orElse: () => signals.first,
    );
  }

  RiskProfile? get risk => _settings?.risk;

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
    await refreshDigest();
  }

  /// Пересчёт идей. [force] — игнорировать свежий кэш (кнопка «Пересчитать»);
  /// без него репозиторий вправе вернуть недавний результат мгновенно.
  Future<void> refreshDigest({bool force = false}) async {
    // Кнопка не должна быть немой: если расчёт уже идёт, честно об этом
    // сказать, а не проглотить нажатие.
    if (_digestLoading) {
      showToast('Расчёт уже идёт${_analysisStage == null ? '' : ': $_analysisStage'}');
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
      await _bridge.notify(id: id++, title: notice.title, body: notice.body);
    }
  }

  /// Автозапуск walk-forward оптимизации, когда пришёл срок (раз в неделю).
  /// Идёт в фоне после дайджеста и не мешает пользоваться приложением.
  void _maybeScheduleOptimization() {
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
      if (!auto) showToast(_errorText(e));
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
      AppSection.capital => AppTab.trades,
      AppSection.trading =>
        _route.pill == TradingPill.ideas.index ? AppTab.ideas : AppTab.trades,
      AppSection.lab =>
        _route.pill == LabPill.screener.index ? AppTab.invest : AppTab.strategies,
      AppSection.control => AppTab.settings,
    };
  }

  void _prefetchForRoute() {
    if (_route.section == AppSection.lab &&
        _route.pill == LabPill.screener.index &&
        _invest == null) {
      refreshInvest();
    }
    if (_route.section == AppSection.today || _route.section == AppSection.capital) {
      refreshCapital();
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

  // ── Счета Т-Инвестиций ─────────────────────────────────────────────────

  List<TInvestAccount> _tinvestAccounts = const [];

  /// Счета, видимые токеном Т-Инвестиций.
  List<TInvestAccount> get tinvestAccounts => _tinvestAccounts;

  /// Счёт, с которого разрешено торговать. null — первый с полным доступом.
  String? get tinvestTradingAccount =>
      tradingDesk?.tradingState.tinvestAccountId;

  /// Спрашивает у брокера список счетов.
  Future<void> refreshTinvestAccounts() async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    _tinvestAccounts = await repository.tinvestAccounts();
    notifyListeners();
  }

  /// Назначает торговый счёт. Остальные остаются на чтение.
  Future<void> setTinvestAccount(String? accountId) async {
    final repository = _repository;
    if (repository is! LocalAnalysisRepository) return;
    await repository.setTinvestAccount(accountId);
    await _reloadSettings();
    showToast(accountId == null
        ? 'Торговый счёт: первый с полным доступом'
        : 'Торговый счёт назначен');
  }

  /// Подпись о свежести данных для шапки раздела.
  ///
  /// Число без времени, к которому оно относится, ничего не стоит: котировки
  /// могли встать полчаса назад, и владелец должен видеть это без перехода в
  /// диагностику.
  String? get dataFreshness {
    final at = _digestFetchedAt;
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
      showToast(_errorText(e));
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
    final desk = investDesk;
    if (desk == null || _investBacktestRunning) return;
    _investBacktestRunning = true;
    notifyListeners();
    try {
      showToast(await desk.optimizeInvestParameters());
    } catch (e) {
      showToast(_errorText(e));
    } finally {
      _investBacktestRunning = false;
      notifyListeners();
    }
  }

  void openSignal(String id) {
    _selectedSignalId = id;
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

    _confirming = true;
    notifyListeners();
    try {
      await _repository.confirmSignal(signal.id);
      _applySignalStatus(signal.id, SignalStatus.working);
      _sheetOpen = false;
      showToast('Ордер отправлен · OCO SL + TP выставлены');
      // Заявка — ещё не позиция. В книгу сделка попадёт исполнением, которое
      // придёт в выписке брокера, поэтому здесь запускается сверка, а не
      // запись «на всякий случай»: капитал не должен меняться от намерения.
      unawaited(refreshCapital(sync: true));
    } catch (e) {
      _sheetOpen = false;
      showToast(_errorText(e));
    } finally {
      _confirming = false;
      notifyListeners();
    }
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
    try {
      showToast(await paper.trackOnPaper(signal.id));
    } catch (e) {
      showToast(_errorText(e));
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
      showToast(_errorText(e));
      notifyListeners();
    }
  }

  Future<void> runBacktest(String strategyId) async {
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
      showToast(_errorText(e));
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
      showToast(_errorText(e));
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
  TradingDesk? get tradingDesk => _desk;

  /// Сохраняет ключи биржи и сразу проверяет их: молча принять нерабочий ключ
  /// значит узнать об этом в момент отправки ордера.
  Future<void> saveBrokerKeys(BrokerId broker, String apiKey, String apiSecret) async {
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
      showToast(_errorText(e));
    }
    await _reloadSettings();
  }

  Future<void> setTradingEnabled(bool enabled) async {
    final desk = _desk;
    if (desk == null) return;
    await desk.setTradingEnabled(enabled);
    showToast(enabled
        ? 'Отправка ордеров включена — каждая сделка всё равно подтверждается'
        : 'Отправка ордеров выключена');
    await _reloadSettings();
  }

  Future<void> setTradingMode(BrokerId broker, TradingMode mode) async {
    final desk = _desk;
    if (desk == null) return;
    try {
      await desk.setTradingMode(broker, mode);
      showToast('${broker.title}: ${mode.labelFor(broker)}');
    } catch (e) {
      showToast(_errorText(e));
    }
    await _reloadSettings();
  }

  Future<void> setKillSwitch(bool on) async {
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
    await _bridge.monitorStop();
    await _lock?.heartbeat(StateLock.ui);
    // Фон мог пересчитать дайджест — перечитываем, чтобы не показывать старое
    // и не гонять расчёт второй раз.
    await refreshDigest();
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
      showToast(_errorText(e));
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
      showToast(_errorText(e));
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
      showToast(_errorText(e));
    }
    notifyListeners();
  }

  // ── Тост ───────────────────────────────────────────────────────────────

  void showToast(String message) {
    _toast = message;
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
