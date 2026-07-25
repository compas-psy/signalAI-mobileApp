import '../domain/models/digest.dart';
import '../domain/models/portfolio.dart';
import '../domain/models/settings.dart';
import '../domain/models/strategy.dart';

/// Контракт данных приложения.
///
/// Мобильный клиент — тонкий (ТЗ §2): он только читает готовые сигналы и
/// отправляет подтверждения. Вся аналитика (ISS/Bybit → скринер → LLM →
/// SignalScore) и весь расчёт риска живут на сервере; ключи бирж на устройство
/// не попадают (ТЗ §11).
///
/// Реализации:
///  * [DemoRepository] — данные макета, работают без сети;
///  * [RestRepository] — HTTP к мобильному гейтвею Server B.
abstract interface class SignalAiRepository {
  /// Утренний дайджест: режим рынка, события, идеи дня.
  Future<DailyDigest> fetchDigest();

  /// Экран «Сделки»: эквити, статистика, активные позиции, журнал.
  Future<TradesSummary> fetchTrades();

  /// Пакеты стратегий, их параметры и последний бэктест.
  Future<StrategiesSnapshot> fetchStrategies();

  /// Подключения бирж, каналы доставки, уведомления, риск-профиль.
  Future<SettingsSnapshot> fetchSettings();

  /// Подтверждение сделки: сервер выставляет лимитку и OCO (SL + TP).
  ///
  /// Вызывается только после биометрии (ТЗ §8). Идемпотентно по [signalId].
  Future<void> confirmSignal(String signalId);

  /// Включение/выключение пакета стратегий.
  Future<void> setStrategyEnabled(String strategyId, bool enabled);

  /// Прогон бэктеста на сервере.
  Future<BacktestResult> runBacktest(String strategyId);

  /// Запрос на подключение биржи (ключи вводятся и хранятся на сервере).
  Future<ExchangeAccount> connectExchange(String exchangeId);

  /// Переключение канала доставки сигналов (пуш / Telegram / MAX).
  Future<void> setChannelEnabled(String channelId, bool enabled);

  /// Переключение правила уведомлений.
  Future<void> setNotificationEnabled(String notificationId, bool enabled);

  /// Обновление политики риска (депозит и % риска на сделку).
  Future<RiskProfile> updateRiskProfile({double? deposit, double? riskPercent});
}
