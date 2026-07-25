import '../../domain/models/digest.dart';
import '../../domain/models/portfolio.dart';
import '../../domain/models/settings.dart';
import '../../domain/models/strategy.dart';
import '../repository.dart';
import 'api_client.dart';

/// Реализация репозитория поверх мобильного гейтвея Server B.
///
/// Контракт эндпоинтов описан в `docs/API.md`. Клиент только читает готовые
/// сигналы и отправляет команды — весь анализ по биржевым API (ISS, Bybit)
/// и весь риск-менеджмент выполняются на сервере (ТЗ §2, §5, §6).
class RestRepository implements SignalAiRepository {
  RestRepository({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  @override
  Future<DailyDigest> fetchDigest() async =>
      DailyDigest.fromJson(await _api.get('/v1/digest'));

  @override
  Future<TradesSummary> fetchTrades() async =>
      TradesSummary.fromJson(await _api.get('/v1/trades'));

  @override
  Future<StrategiesSnapshot> fetchStrategies() async {
    final json = await _api.get('/v1/strategies');
    return StrategiesSnapshot(
      packs: (json['packs'] as List<dynamic>? ?? const [])
          .map((e) => StrategyPack.fromJson(e as Map<String, dynamic>))
          .toList(growable: false),
      paramsTitle: json['params_title'] as String? ?? '',
      params: (json['params'] as List<dynamic>? ?? const [])
          .map((e) => StrategyParam.fromJson(e as Map<String, dynamic>))
          .toList(growable: false),
      backtest: BacktestResult.fromJson(
        json['backtest'] as Map<String, dynamic>? ?? const {},
      ),
    );
  }

  @override
  Future<SettingsSnapshot> fetchSettings() async {
    final json = await _api.get('/v1/settings');
    return SettingsSnapshot(
      exchanges: (json['exchanges'] as List<dynamic>? ?? const [])
          .map((e) => ExchangeAccount.fromJson(e as Map<String, dynamic>))
          .toList(growable: false),
      channels: (json['channels'] as List<dynamic>? ?? const [])
          .map((e) => ToggleSetting.fromJson(e as Map<String, dynamic>))
          .toList(growable: false),
      notifications: (json['notifications'] as List<dynamic>? ?? const [])
          .map((e) => ToggleSetting.fromJson(e as Map<String, dynamic>))
          .toList(growable: false),
      risk: RiskProfile.fromJson(json['risk'] as Map<String, dynamic>),
    );
  }

  @override
  Future<void> confirmSignal(String signalId) async {
    // Идемпотентность по сигналу: повторное нажатие не создаёт второй ордер.
    await _api.post('/v1/signals/$signalId/confirm', idempotencyKey: 'confirm:$signalId');
  }

  @override
  Future<void> setStrategyEnabled(String strategyId, bool enabled) async {
    await _api.post('/v1/strategies/$strategyId/enabled', body: {'enabled': enabled});
  }

  @override
  Future<BacktestResult> runBacktest(String strategyId) async =>
      BacktestResult.fromJson(await _api.post('/v1/strategies/$strategyId/backtest'));

  @override
  Future<ExchangeAccount> connectExchange(String exchangeId) async =>
      ExchangeAccount.fromJson(await _api.post('/v1/exchanges/$exchangeId/connect'));

  @override
  Future<void> setChannelEnabled(String channelId, bool enabled) async {
    await _api.post('/v1/channels/$channelId', body: {'enabled': enabled});
  }

  @override
  Future<void> setNotificationEnabled(String notificationId, bool enabled) async {
    await _api.post('/v1/notifications/$notificationId', body: {'enabled': enabled});
  }

  @override
  Future<RiskProfile> updateRiskProfile({double? deposit, double? riskPercent}) async =>
      RiskProfile.fromJson(
        await _api.patch('/v1/risk-profile', body: {
          'deposit': ?deposit,
          'risk_percent': ?riskPercent,
        }),
      );
}
