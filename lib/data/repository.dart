import '../domain/broker/broker.dart';
import '../domain/broker/trading_gate.dart';
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
/// Функция ещё не реализована — и приложение говорит об этом прямо.
///
/// Отличается от сетевой ошибки принципиально: «нет связи с сервером» в
/// автономном режиме — ложь, сервера там нет вовсе. [message] показывается
/// пользователю как есть.
class FeatureUnavailableException implements Exception {
  const FeatureUnavailableException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// Репозиторий, умеющий сообщать о ходе долгого расчёта.
///
/// Автономный анализ занимает секунды: слушатель получает человекочитаемые
/// стадии («Анализ SiU6…»), чтобы пользователь видел живой прогресс, а не
/// зависшую заставку.
abstract interface class ProgressReporting {
  set onProgress(void Function(String stage)? listener);
}

/// Репозиторий, умеющий подбирать параметры стратегии walk-forward
/// оптимизацией на устройстве.
abstract interface class ParameterOptimizing {
  /// Пора ли пересчитывать параметры (по расписанию — раз в неделю).
  bool get optimizationDue;

  /// Прогон оптимизации по включённым стратегиям. Возвращает короткий
  /// человекочитаемый итог для тоста.
  Future<String> optimizeParameters();
}

/// Репозиторий, умеющий торговать: ключи, режим, гейты и отправка ордера.
///
/// Отдельный интерфейс, а не часть [SignalAiRepository]: анализ работает без
/// торгового доступа, и реализация без брокера — это нормальный режим, а не
/// урезанный. Экран проверяет `is TradingDesk` и просто не рисует торговый
/// блок, если торговать нечем.
abstract interface class TradingDesk {
  /// Текущее состояние контура: режим, включённость, аварийная остановка.
  TradingState get tradingState;

  /// Допуск к живым деньгам по бумажной статистике.
  GateVerdict get liveGate;

  /// Есть ли ключи для текущего режима.
  Future<bool> get hasBrokerKeys;

  /// Сохраняет ключи и сразу проверяет их на бирже. Возвращает ответ биржи.
  Future<String> saveBrokerKeys({
    required TradingMode mode,
    required String apiKey,
    required String apiSecret,
  });

  Future<void> setTradingEnabled(bool enabled);

  /// Смена режима. Переход в live разрешён только при открытом [liveGate].
  Future<void> setTradingMode(TradingMode mode);

  /// Аварийная остановка: снимает заявки и запрещает новые. Снимается руками.
  Future<String> setKillSwitch(bool on);

  /// Позиции, как их видит биржа.
  Future<List<BrokerPosition>> brokerPositions();
}

abstract interface class SignalAiRepository {
  /// Утренний дайджест: режим рынка, события, идеи дня.
  ///
  /// [force] — пересчитать заново, игнорируя свежий кэш. Без него реализация
  /// вправе вернуть недавний результат: рынок не меняется настолько, чтобы
  /// пересчитывать всё при каждом переключении вкладки.
  Future<DailyDigest> fetchDigest({bool force = false});

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
