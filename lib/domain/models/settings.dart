import '../broker/broker.dart';

/// Подключение к брокеру (MOEX) или крипто-бирже (ТЗ §7).
///
/// Ключи живут только на сервере (ТЗ §11) — сюда приходит лишь статус.
class ExchangeAccount {
  const ExchangeAccount({
    required this.id,
    required this.abbr,
    required this.name,
    required this.subtitle,
    required this.connected,
    required this.accentHex,
    this.isDataSource = false,
  });

  final String id;

  /// Короткая метка в квадратике: «T», «B», «BN».
  final String abbr;
  final String name;

  /// «MOEX: фьючерсы и акции · токен активен».
  final String subtitle;
  final bool connected;

  /// Фирменный цвет метки, 0xAARRGGBB.
  final int accentHex;

  /// Источник рыночных данных (котировки/свечи), а не торговый доступ.
  ///
  /// Разница принципиальна и показывается в интерфейсе отдельными секциями:
  /// «подключено» у источника данных не означает, что можно торговать.
  final bool isDataSource;

  ExchangeAccount copyWith({bool? connected, String? subtitle}) => ExchangeAccount(
        id: id,
        abbr: abbr,
        name: name,
        subtitle: subtitle ?? this.subtitle,
        connected: connected ?? this.connected,
        accentHex: accentHex,
        isDataSource: isDataSource,
      );

  factory ExchangeAccount.fromJson(Map<String, dynamic> j) => ExchangeAccount(
        id: j['id'] as String,
        abbr: j['abbr'] as String,
        name: j['name'] as String,
        subtitle: j['subtitle'] as String? ?? '',
        connected: j['connected'] as bool? ?? false,
        accentHex: int.parse(
          (j['accent'] as String? ?? '#8E8E98').replaceFirst('#', 'FF'),
          radix: 16,
        ),
        isDataSource: j['is_data_source'] as bool? ?? false,
      );
}

/// Переключаемая настройка: канал доставки или правило уведомлений.
class ToggleSetting {
  const ToggleSetting({
    required this.id,
    required this.name,
    required this.subtitle,
    required this.enabled,
  });

  final String id;
  final String name;
  final String subtitle;
  final bool enabled;

  ToggleSetting copyWith({bool? enabled}) => ToggleSetting(
        id: id,
        name: name,
        subtitle: subtitle,
        enabled: enabled ?? this.enabled,
      );

  factory ToggleSetting.fromJson(Map<String, dynamic> j) => ToggleSetting(
        id: j['id'] as String,
        name: j['name'] as String,
        subtitle: j['subtitle'] as String? ?? '',
        enabled: j['enabled'] as bool? ?? false,
      );
}

/// Риск-профиль (ТЗ §6). Депозит и ГО приходят с сервера из брокерского API —
/// руками не вводятся; редактируется только политика риска.
class RiskProfile {
  const RiskProfile({
    required this.deposit,
    required this.riskPercent,
    required this.dailyLossLimit,
    required this.maxConcurrentTrades,
    required this.pauseRule,
  });

  final double deposit;

  /// Риск на сделку в процентах депозита.
  final double riskPercent;

  /// «−2% · автостоп».
  final String dailyLossLimit;

  /// «до 3».
  final String maxConcurrentTrades;

  /// «пауза до завтра».
  final String pauseRule;

  /// Риск на сделку в рублях.
  double get riskRub => (deposit * riskPercent / 100).roundToDouble();

  RiskProfile copyWith({double? deposit, double? riskPercent}) => RiskProfile(
        deposit: deposit ?? this.deposit,
        riskPercent: riskPercent ?? this.riskPercent,
        dailyLossLimit: dailyLossLimit,
        maxConcurrentTrades: maxConcurrentTrades,
        pauseRule: pauseRule,
      );

  factory RiskProfile.fromJson(Map<String, dynamic> j) => RiskProfile(
        deposit: (j['deposit'] as num).toDouble(),
        riskPercent: (j['risk_percent'] as num).toDouble(),
        dailyLossLimit: j['daily_loss_limit'] as String? ?? '',
        maxConcurrentTrades: j['max_concurrent_trades'] as String? ?? '',
        pauseRule: j['pause_rule'] as String? ?? '',
      );
}

/// Данные экрана «Настройки».
/// Состояние одной торговой площадки.
class BrokerView {
  const BrokerView({
    required this.id,
    required this.title,
    required this.modeLabel,
    required this.live,
    required this.hasKeys,
    required this.liveAllowed,
    required this.liveBlockedReason,
    this.keyNote = '',
    this.keysAccepted = false,
  });

  /// Идентификатор площадки строкой — интерфейсу хватает его для вызовов.
  final String id;
  final String title;
  final String modeLabel;

  /// Площадка стоит на живых деньгах.
  final bool live;
  final bool hasKeys;

  /// Можно ли перевести эту площадку в живой режим прямо сейчас.
  final bool liveAllowed;

  /// Почему нельзя. Пусто, если можно.
  final String liveBlockedReason;

  /// Итог последней проверки ключей у биржи: её ответ либо причина отказа.
  ///
  /// Причина живёт дальше тоста: ключ, который биржа не приняла, обязан
  /// объяснять это и через сутки, а не оставлять владельца гадать.
  final String keyNote;

  /// Ключи есть и биржа их приняла. Только в этом состоянии площадка
  /// действительно может отправить заявку.
  final bool keysAccepted;
}

/// Состояние торгового контура для настроек.
///
/// Показывается ровно то, от чего зависит, уйдёт ли ордер: площадки с их
/// режимами и ключами, общая включённость, аварийная остановка и допуск.
/// Ничего декоративного здесь быть не должно.
class TradingView {
  const TradingView({
    required this.brokers,
    required this.enabled,
    required this.killSwitch,
    required this.gateAllowed,
    required this.gateReason,
    required this.gateProgress,
    required this.vaultAvailable,
    required this.confirmMethod,
  });

  final List<BrokerView> brokers;
  final bool enabled;
  final bool killSwitch;

  /// Открыт ли допуск к живым деньгам по журналу бумажных сделок.
  final bool gateAllowed;
  final String gateReason;
  final double gateProgress;

  /// Есть ли на устройстве защищённое хранилище и чем подтверждать сделку.
  final bool vaultAvailable;
  final ConfirmMethod confirmMethod;

  bool get biometricsAvailable => confirmMethod.available;

  /// Можно ли прямо сейчас отправить ордер хоть куда-нибудь.
  ///
  /// Считается по всей цепочке, а не по её началу. Раньше здесь хватало
  /// заданных ключей — и контур показывал «ГОТОВ» на устройстве, где сделку
  /// подтвердить нечем и ни один ордер уйти не мог. Зелёный бейдж, который
  /// врёт, хуже красного: именно ему верят, когда решают не проверять.
  bool get ready =>
      enabled &&
      !killSwitch &&
      vaultAvailable &&
      confirmMethod.available &&
      brokers.any((b) => b.keysAccepted);

  /// Чего не хватает до готовности. Пусто, если всё на месте.
  String get blockingReason {
    if (killSwitch) return 'активна аварийная остановка';
    if (!vaultAvailable) return 'нет защищённого хранилища для ключей';
    if (!confirmMethod.available) return 'сделку нечем подтвердить';
    if (!brokers.any((b) => b.keysAccepted)) {
      return brokers.any((b) => b.hasKeys)
          ? 'ни одна площадка не подтвердила ключи'
          : 'ключи площадок не заданы';
    }
    if (!enabled) return 'отправка ордеров выключена';
    return '';
  }
}

/// Состояние фонового контура для настроек.
class BackgroundView {
  const BackgroundView({
    required this.modeLabel,
    required this.persistent,
    required this.enabled,
    required this.stateNote,
  });

  final String modeLabel;

  /// Постоянный сервис, а не короткие пробуждения.
  final bool persistent;

  /// Включён ли фоновый контур вообще.
  final bool enabled;

  /// «работает · последний прогон 14:03» либо причина отказа.
  final String stateNote;
}

class SettingsSnapshot {
  const SettingsSnapshot({
    required this.exchanges,
    required this.channels,
    required this.notifications,
    required this.risk,
    this.trading,
    this.background,
  });

  final List<ExchangeAccount> exchanges;
  final List<ToggleSetting> channels;
  final List<ToggleSetting> notifications;
  final RiskProfile risk;

  /// Торговый контур. null — режим без исполнения сделок (демо).
  final TradingView? trading;

  /// Фоновый контур. null — режим без фоновой работы (демо).
  final BackgroundView? background;

  SettingsSnapshot copyWith({
    List<ExchangeAccount>? exchanges,
    List<ToggleSetting>? channels,
    List<ToggleSetting>? notifications,
    RiskProfile? risk,
    TradingView? trading,
    BackgroundView? background,
  }) =>
      SettingsSnapshot(
        exchanges: exchanges ?? this.exchanges,
        channels: channels ?? this.channels,
        notifications: notifications ?? this.notifications,
        risk: risk ?? this.risk,
        trading: trading ?? this.trading,
        background: background ?? this.background,
      );
}
