import '../ledger/signal_ledger.dart';
import 'broker.dart';

/// Допуск стратегии к живым деньгам и аварийная остановка.
///
/// Правило одно и оно не обсуждается в момент, когда хочется зайти: живой счёт
/// открывается только после того, как стратегия отработала на бумаге
/// достаточную выборку и оказалась прибыльной. Порог проверяется по журналу
/// сигналов — по сделкам, прожитым вперёд по реальным свечам с издержками,
/// а не по бэктесту, который всегда можно подогнать.
class LiveTradingGate {
  const LiveTradingGate({
    this.minClosedPaperTrades = 20,
    this.minProfitFactor = 1.0,
  });

  /// Сколько закрытых бумажных сделок нужно накопить.
  final int minClosedPaperTrades;

  /// Профит-фактор, ниже которого живая торговля не открывается.
  final double minProfitFactor;

  /// Можно ли включать живой режим прямо сейчас.
  GateVerdict evaluate(SignalLedger ledger) {
    final closed = ledger.closed.length;
    if (closed < minClosedPaperTrades) {
      return GateVerdict(
        allowed: false,
        reason: 'нужно $minClosedPaperTrades закрытых бумажных сделок, есть $closed',
        progress: closed / minClosedPaperTrades,
      );
    }
    final pf = ledger.profitFactor;
    // null означает «убытков не было»: на такой выборке это не признак
    // качества, а признак того, что рынок ещё не проверял стратегию на прочность.
    if (pf == null) {
      return const GateVerdict(
        allowed: true,
        reason: 'убыточных сделок пока не было',
        progress: 1,
      );
    }
    if (pf < minProfitFactor) {
      return GateVerdict(
        allowed: false,
        reason: 'профит-фактор ${_num(pf)} ниже ${_num(minProfitFactor)} — '
            'на бумаге стратегия не зарабатывает',
        progress: 1,
      );
    }
    return GateVerdict(
      allowed: true,
      reason: '$closed закрытых сделок, PF ${_num(pf)}',
      progress: 1,
    );
  }

  static String _num(double v) => v.toStringAsFixed(1).replaceAll('.', ',');
}

/// Вердикт допуска.
class GateVerdict {
  const GateVerdict({
    required this.allowed,
    required this.reason,
    required this.progress,
  });

  final bool allowed;
  final String reason;

  /// Доля пути до допуска, 0…1 — чтобы прогресс был виден, а не только запрет.
  final double progress;
}

/// Состояние торгового контура: режимы площадок, включённость, остановка.
///
/// Режим — свойство площадки, а не приложения: крипта может стоять на testnet,
/// пока российский счёт ещё в песочнице, и это независимые решения. А вот
/// аварийная остановка одна на всё: выключатель «стоп, всё» перестаёт быть
/// выключателем, если его надо нажимать в нескольких местах.
class TradingState {
  const TradingState({
    Map<BrokerId, TradingMode>? modes,
    this.enabled = false,
    this.killSwitch = false,
    this.tinvestAccountId,
    this.allowedAccountIds = const {},
  }) : _modes = modes ?? const {};

  final Map<BrokerId, TradingMode> _modes;

  /// Включена ли отправка ордеров вообще.
  final bool enabled;

  /// Аварийная остановка: заявки сняты, новые не отправляются.
  ///
  /// Снимается только руками. Это специально: выключатель, который сам себя
  /// возвращает в исходное, не выключатель.
  final bool killSwitch;

  /// Счёт Т-Инвестиций, с которого уходят заявки.
  ///
  /// Токен видит все счета владельца, но торговать приложение имеет право
  /// только с одного — выбранного осознанно. null — первый с полным
  /// доступом; остальные счета читаются для капитала и не торгуют.
  final String? tinvestAccountId;

  /// Счета, которые приложению разрешено видеть вообще.
  ///
  /// Пустое множество значит «доступ ещё не выдан»: тогда не показывается ни
  /// один счёт. Это не осторожность ради осторожности — токен Invest API
  /// привязан к пользователю, а не к счёту, и видит их все. Ограничить его
  /// на стороне брокера нельзя, значит ограничивает приложение, и делает это
  /// явным списком, а не умолчанием «всё, что нашлось».
  ///
  /// Разрешение читать — не разрешение торговать. Заявки уходят только с
  /// [tinvestAccountId], и только по идеям: инвестиционный контур даёт
  /// рекомендации, а сделки по ним владелец совершает сам.
  final Set<String> allowedAccountIds;

  /// Разрешён ли счёт к чтению.
  bool allows(String accountId) => allowedAccountIds.contains(accountId);

  /// Можно ли отправлять заявки с этого счёта.
  ///
  /// Ровно один счёт и ровно при условии, что он же разрешён к чтению.
  /// Торговый счёт, выпавший из списка доступа, торговым быть перестаёт —
  /// иначе отзыв доступа не отзывал бы главного.
  bool canTradeFrom(String accountId) =>
      accountId.isNotEmpty &&
      accountId == tinvestAccountId &&
      allows(accountId);

  /// Режим площадки. По умолчанию тренировочный — живой режим включается
  /// только осознанно и только через допуск.
  TradingMode modeOf(BrokerId broker) => _modes[broker] ?? TradingMode.testnet;

  bool get canSendOrders => enabled && !killSwitch;

  /// Есть ли хоть одна площадка на живых деньгах.
  bool get anyLive => BrokerId.values.any((b) => modeOf(b) == TradingMode.live);

  TradingState copyWith({
    bool? enabled,
    bool? killSwitch,
    String? tinvestAccountId,
    Set<String>? allowedAccountIds,
  }) =>
      TradingState(
        modes: _modes,
        enabled: enabled ?? this.enabled,
        killSwitch: killSwitch ?? this.killSwitch,
        tinvestAccountId: tinvestAccountId ?? this.tinvestAccountId,
        allowedAccountIds: allowedAccountIds ?? this.allowedAccountIds,
      );

  TradingState withMode(BrokerId broker, TradingMode mode) => TradingState(
        modes: {..._modes, broker: mode},
        enabled: enabled,
        killSwitch: killSwitch,
        tinvestAccountId: tinvestAccountId,
        allowedAccountIds: allowedAccountIds,
      );

  Map<String, dynamic> toJson() => {
        'modes': {for (final e in _modes.entries) e.key.name: e.value.name},
        'enabled': enabled,
        'kill_switch': killSwitch,
        if (tinvestAccountId != null) 'tinvest_account': tinvestAccountId,
        'allowed_accounts': allowedAccountIds.toList(),
      };

  factory TradingState.fromJson(Map<String, dynamic> j) {
    final modes = <BrokerId, TradingMode>{};
    final stored = j['modes'] as Map<String, dynamic>?;
    if (stored != null) {
      for (final entry in stored.entries) {
        modes[BrokerId.parse(entry.key)] = TradingMode.parse(entry.value as String? ?? '');
      }
    } else if (j['mode'] != null) {
      // Состояние старой версии: единственный режим относился к Bybit —
      // единственной тогда площадке. Терять его при обновлении нельзя.
      modes[BrokerId.bybit] = TradingMode.parse(j['mode'] as String);
    }
    return TradingState(
      modes: modes,
      enabled: j['enabled'] as bool? ?? false,
      killSwitch: j['kill_switch'] as bool? ?? false,
      tinvestAccountId: j['tinvest_account'] as String?,
      // Список отсутствует у состояния старых версий. Пустой — значит
      // доступ ещё не выдавали, и ни один счёт не читается. Подставить сюда
      // «все» было бы тихой выдачей доступа при обновлении приложения.
      allowedAccountIds: {
        for (final id in j['allowed_accounts'] as List<dynamic>? ?? const [])
          id as String,
      },
    );
  }
}
