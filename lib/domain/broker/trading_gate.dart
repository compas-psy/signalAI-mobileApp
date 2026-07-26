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

/// Состояние торгового контура: режим, аварийная остановка, готовность.
class TradingState {
  const TradingState({
    this.mode = TradingMode.testnet,
    this.enabled = false,
    this.killSwitch = false,
  });

  final TradingMode mode;

  /// Включена ли автоматическая отправка ордеров вообще.
  final bool enabled;

  /// Аварийная остановка: заявки сняты, новые не отправляются.
  ///
  /// Снимается только руками. Это специально: выключатель, который сам себя
  /// возвращает в исходное, не выключатель.
  final bool killSwitch;

  bool get canSendOrders => enabled && !killSwitch;

  TradingState copyWith({TradingMode? mode, bool? enabled, bool? killSwitch}) => TradingState(
        mode: mode ?? this.mode,
        enabled: enabled ?? this.enabled,
        killSwitch: killSwitch ?? this.killSwitch,
      );

  Map<String, dynamic> toJson() => {
        'mode': mode.name,
        'enabled': enabled,
        'kill_switch': killSwitch,
      };

  factory TradingState.fromJson(Map<String, dynamic> j) => TradingState(
        mode: TradingMode.parse(j['mode'] as String? ?? 'testnet'),
        enabled: j['enabled'] as bool? ?? false,
        killSwitch: j['kill_switch'] as bool? ?? false,
      );
}
