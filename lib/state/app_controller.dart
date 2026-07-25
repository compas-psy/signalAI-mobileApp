import 'dart:async';

import 'package:flutter/foundation.dart';

import '../data/api/api_client.dart';
import '../data/repository.dart';
import '../domain/enums.dart';
import '../domain/models/digest.dart';
import '../domain/models/portfolio.dart';
import '../domain/models/settings.dart';
import '../domain/models/signal.dart';
import '../domain/models/strategy.dart';

/// Вкладки нижней навигации.
enum AppTab { ideas, trades, strategies, settings }

/// Состояние приложения: что показываем и что сейчас делает пользователь.
///
/// Никакой торговой логики здесь нет — только загрузка данных, навигация и
/// оптимистичные переключатели. Решения принимает сервер (ТЗ §2).
class AppController extends ChangeNotifier {
  AppController(this._repository);

  final SignalAiRepository _repository;

  AppTab _tab = AppTab.ideas;
  String? _selectedSignalId;
  bool _sheetOpen = false;
  String? _toast;
  Timer? _toastTimer;
  bool _backtestRunning = false;
  bool _confirming = false;

  DailyDigest? _digest;
  TradesSummary? _trades;
  StrategiesSnapshot? _strategies;
  SettingsSnapshot? _settings;
  Object? _error;

  AppTab get tab => _tab;
  bool get sheetOpen => _sheetOpen;
  String? get toast => _toast;
  bool get backtestRunning => _backtestRunning;
  bool get confirming => _confirming;
  DailyDigest? get digest => _digest;
  TradesSummary? get trades => _trades;
  StrategiesSnapshot? get strategies => _strategies;
  SettingsSnapshot? get settings => _settings;
  Object? get error => _error;

  bool get isLoading => _digest == null && _error == null;

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

  Future<void> load() async {
    try {
      _error = null;
      final results = await Future.wait([
        _repository.fetchDigest(),
        _repository.fetchTrades(),
        _repository.fetchStrategies(),
        _repository.fetchSettings(),
      ]);
      _digest = results[0] as DailyDigest;
      _trades = results[1] as TradesSummary;
      _strategies = results[2] as StrategiesSnapshot;
      _settings = results[3] as SettingsSnapshot;
    } catch (e) {
      _error = e;
    }
    notifyListeners();
  }

  // ── Навигация ──────────────────────────────────────────────────────────

  void goTab(AppTab tab) {
    _tab = tab;
    _selectedSignalId = null;
    _sheetOpen = false;
    notifyListeners();
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
    } catch (e) {
      _sheetOpen = false;
      showToast(_errorText(e));
    } finally {
      _confirming = false;
      notifyListeners();
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
    notifyListeners();
    try {
      final result = await _repository.runBacktest(strategyId);
      _strategies = _strategies?.copyWith(backtest: result);
      showToast('Бэктест завершён · PF ${_profitFactor(result)}');
    } catch (e) {
      showToast(_errorText(e));
    } finally {
      _backtestRunning = false;
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

  Future<void> toggleChannel(String id, bool enabled) async {
    final snapshot = _settings;
    if (snapshot == null) return;
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

  String _errorText(Object e) =>
      e is ApiException ? e.message : 'Нет связи с сервером — попробуйте ещё раз';

  @override
  void dispose() {
    _toastTimer?.cancel();
    super.dispose();
  }
}
