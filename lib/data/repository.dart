import '../domain/broker/broker.dart';
import 'ledger/capital_desk.dart';
import '../domain/invest/invest_models.dart';
import '../domain/ledger/signal_ledger.dart';
import '../domain/broker/trading_diagnostics.dart';
import '../domain/broker/trading_gate.dart';
import '../domain/models/digest.dart';
import '../monitor/background_mode.dart';
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

  /// Есть ли ключи площадки для её текущего режима.
  Future<bool> hasBrokerKeys(BrokerId broker);

  /// Режимы площадки, для которых ключи реально лежат в хранилище.
  ///
  /// Ключ привязан к паре «площадка + режим», и это правильно: перепутать
  /// тренировочный счёт с живым — самая дорогая опечатка. Но из-за этого
  /// расхождение режима раньше выглядело как «ключей нет вовсе»: площадка
  /// молча исчезала из капитала. Чтение капитала теперь может взять режим,
  /// для которого ключи есть, и сказать об этом. Отправка ордера — не может.
  Future<Set<TradingMode>> brokerKeyModes(BrokerId broker);

  /// Режим, которым безопасно **читать** площадку: текущий, если для него есть
  /// ключи, иначе любой другой с ключами. null — ключей нет ни для одного.
  Future<TradingMode?> readableMode(BrokerId broker);

  /// Сохраняет ключи и сразу проверяет их у брокера. Возвращает его ответ.
  Future<String> saveBrokerKeys({
    required BrokerId broker,
    required TradingMode mode,
    required String apiKey,
    required String apiSecret,
  });

  Future<void> setTradingEnabled(bool enabled);

  /// Смена режима площадки. Переход в live разрешён только при открытом
  /// [liveGate] — и не для всех площадок.
  Future<void> setTradingMode(BrokerId broker, TradingMode mode);

  /// Аварийная остановка: снимает заявки и запрещает новые. Снимается руками.
  Future<String> setKillSwitch(bool on);

  /// Позиции, как их видит биржа.
  Future<List<BrokerPosition>> brokerPositions();

  /// Режим фоновой работы и включён ли контур.
  BackgroundMode get backgroundMode;
  bool get backgroundEnabled;

  /// Смена режима. Контур перезапускается, если он был включён.
  Future<void> setBackgroundMode(BackgroundMode mode);

  Future<void> setBackgroundEnabled(bool enabled);

  /// Итог последнего фонового прогона — для honest-подписи в настройках.
  Future<String> backgroundStateNote();

  /// Живой прогон торгового контура: хранилище, подтверждение, ключи, счета.
  ///
  /// Нужен ровно затем, зачем и диагностика данных: чтобы «работает или нет»
  /// был проверяемым фактом, а не ощущением от зелёной подписи.
  Stream<TradingCheck> diagnoseTrading();
}

/// Раздел «Инвест»: среднесрочные идеи по акциям с дневным пересчётом.
///
/// Отдельная способность: раздел живёт только в автономном режиме, где
/// дневки считаются на устройстве, а фундаментальные паспорта приходят из
/// Invest API. Исполнение — руками; автоторговли здесь нет по построению.
abstract interface class InvestDesk {
  /// Выдача раздела. [force] — пересчитать сейчас, не дожидаясь ночи.
  Future<InvestDigest> fetchInvestDigest({bool force = false});

  /// Отдельный журнал бумажных сделок раздела.
  SignalLedger get investLedger;

  /// Бэктест дневной стратегии по ликвидным акциям.
  Future<BacktestResult> runInvestBacktest();

  /// Последний бэктест. null — ещё не запускался.
  BacktestResult? get investBacktest;

  /// Walk-forward подбор параметров дневной стратегии.
  Future<String> optimizeInvestParameters();
}

/// Репозиторий, ведущий журнал бумажных сделок.
///
/// Отдельная способность: бумажный журнал есть только у автономного режима,
/// где сделки проживаются по реальным свечам на устройстве.
abstract interface class PaperTracking {
  /// Как идея по этому инструменту записана в журнале. null — не ведётся.
  String? paperNoteFor(String symbol);

  /// Завести идею в журнал вручную. Возвращает строку для тоста.
  Future<String> trackOnPaper(String signalId);
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

/// Репозиторий, ведущий книгу капитала.
///
/// Отдельная способность по той же причине, что и торговый контур: анализ
/// работает и без учёта, а книга без брокерских ключей работает на ручных
/// операциях. Экран проверяет `is CapitalKeeper` и не рисует раздел, если
/// вести капитал нечем.
abstract interface class CapitalKeeper {
  /// Книга и производные состояния.
  CapitalDesk get capital;

  /// Синхронизация с площадками: обновляет счета, переоценку позиций и
  /// статусы сверки. Возвращает человекочитаемый итог для подписи.
  Future<String> syncCapital();
}
