import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/enums.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/settings.dart';
import '../../domain/models/signal.dart';
import '../../domain/position_sizing.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'segmented.dart';

/// Параметры сделки одним блоком: уровни, риск, сопровождение.
class PlanCard extends StatelessWidget {
  const PlanCard({
    super.key,
    required this.signal,
    required this.idea,
    required this.risk,
  });

  final TradingSignal signal;

  /// Идея движка вместе с планом. null — плана нет, показываем уровни
  /// сигнала: они те же самые, но без объёма и отпечатка.
  final Idea? idea;

  final RiskProfile risk;

  TradePlan? get _plan => idea?.plan;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Параметры сделки')),
                _ReadinessChip(state: idea?.state, status: signal.status),
              ],
            ),
            const SizedBox(height: 11),
            TileGrid(tiles: _levelTiles()),
            const SizedBox(height: 8),
            TileGrid(tiles: _riskTiles()),
            _rateNote(),
            const SizedBox(height: 12),
            _management(),
          ],
        ),
      );

  List<Widget> _levelTiles() {
    final plan = _plan;
    final decimals = signal.priceDecimals;
    final tiles = <Widget>[
      MetricTile(
        // Главное число — именно цена заявки, которую сервер переносит в
        // paper-сделку (`entry_reference`). Границы зоны — допустимый коридор,
        // а не две конкурирующие «цены входа».
        label: plan == null
            ? (signal.entryIsStop ? 'Вход · стоп' : 'Вход · лимит')
            : 'Вход · ${plan.orderType.label.toLowerCase()}',
        value: fmtPrice(plan?.entry ?? signal.entry, decimals),
        color: C.accent,
        hint: plan == null
            ? null
            : 'зона ${fmtPrice(plan.entryLow, decimals)}–${fmtPrice(plan.entryHigh, decimals)}',
      ),
      MetricTile(
        label: 'Стоп',
        value: fmtPrice(plan?.stop ?? signal.stopLoss, decimals),
        color: C.red,
      ),
    ];
    if (plan != null && plan.targets.isNotEmpty) {
      for (final target in plan.targets) {
        tiles.add(MetricTile(
          label: target.name,
          value: fmtPrice(target.price, decimals),
          color: C.green,
          hint: '${(target.fraction * 100).round()}% · '
              '${_r(plan.rrTo(target))}R',
        ));
      }
    } else {
      for (final tp in signal.takeProfits) {
        tiles.add(MetricTile(
          label: tp.label,
          value: fmtPrice(tp.price, decimals),
          color: C.green,
          hint: '${tp.sharePercent}% · ${PositionSizing.takeProfitR(signal, tp)}',
        ));
      }
    }
    return tiles;
  }

  List<Widget> _riskTiles() {
    final plan = _plan;
    final riskRub = plan?.riskRubles ?? risk.riskRub;
    final riskPct = plan?.riskPercent ?? risk.riskPercent;
    return [
      MetricTile(
        label: 'Риск',
        value: '${fmt(riskRub, 0)} ₽',
        hint: 'из ${fmt(risk.deposit, 0)} ₽',
      ),
      MetricTile(
        label: 'Доля счёта',
        value: riskPercentLabel(riskPct),
        hint: plan == null ? 'профиль риска' : 'бюджет движка',
      ),
      MetricTile(
        label: 'Размер',
        value: plan == null
            ? PositionSizing.quantityLabel(signal, risk)
            : _quantityLabel(plan),
        color: C.accent,
        hint: plan == null ? signal.unitRiskLabel : _perUnitHint(plan),
      ),
      MetricTile(
        label: 'R:R',
        value: plan == null ? signal.riskReward : _r(plan.rrToSecondTarget),
        hint: 'до TP2',
      ),
      MetricTile(
        label: 'Прибыль',
        // Для серверного плана считаем деньги из движения цены, объёма и
        // стоимости пункта. У FORTS valuePerPoint восстанавливается из
        // биржевых MINSTEP/STEPPRICE, у crypto применяется курс к рублю.
        value: '+${fmt(plan == null ? PositionSizing.potentialProfitRub(signal, risk) : _potentialProfitRubles(plan), 0)} ₽',
        color: C.green,
        hint: 'все тейки с долями',
      ),
      MetricTile(
        label: 'Убыток',
        // Стоп-убыток — фактический risk_amount сервера: именно он уже
        // учитывает округление количества и модель исполнения.
        value: '−${fmt(plan?.riskRubles ?? PositionSizing.potentialLossRub(signal, risk), 0)} ₽',
        color: C.red,
        hint: 'если сработает стоп',
      ),
    ];
  }

  /// Gross P/L по всем целям в рублях при рассчитанном количестве.
  ///
  /// Никаких таблиц коэффициентов в приложении нет. Сервер уже передал
  /// `valuePerPoint`: для MOEX это STEPPRICE / MINSTEP; для линейной crypto —
  /// стоимость пункта в валюте котировки. Поэтому один и тот же расчёт
  /// корректен и для PXU6, и для RTS, Brent, валютных и crypto-планов.
  static double _potentialProfitRubles(TradePlan plan) {
    if (plan.quantity <= 0 || plan.valuePerPoint <= 0) return 0;
    final rate = plan.quoteCurrency == 'RUB' ? 1.0 : plan.quoteRateRub;
    if (rate == null || rate <= 0) return 0;

    var total = 0.0;
    for (final target in plan.targets) {
      final move = plan.direction.isLong
          ? target.price - plan.entry
          : plan.entry - target.price;
      total += target.fraction * move * plan.valuePerPoint * plan.quantity * rate;
    }
    return total > 0 ? total : 0;
  }

  /// Объём в номинале инструмента.
  static String _quantityLabel(TradePlan plan) {
    final unit = plan.quantityUnit;
    final value = fmt(plan.quantity, plan.quantityDecimals);
    return unit.isEmpty ? value : '$value $unit';
  }

  /// Подпись под объёмом: денежный риск до стопа на одну единицу позиции.
  static String _perUnitHint(TradePlan plan) {
    final perUnit = plan.riskPerUnit;
    if (perUnit <= 0) return '';
    final currency = plan.quoteCurrency == 'RUB' ? '₽' : plan.quoteCurrency;
    final unit = plan.quantityUnit.isEmpty ? 'единицу' : plan.quantityUnit;
    return 'стоп ${fmt(perUnit, perUnit < 10 ? 2 : 0)} $currency за $unit';
  }

  /// Курс, по которому получены рубли.
  Widget _rateNote() {
    final plan = _plan;
    final note = plan?.quoteNote ?? '';
    if (plan == null || note.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Text(
        'Прибыль и убыток пересчитаны в рубли: $note',
        style: T.body(10.5, color: C.faint, height: 1.4),
      ),
    );
  }

  /// Сопровождение — правила, которые подписываются вместе с входом.
  Widget _management() {
    final rules = _plan?.stopManagement ?? const <String>[];
    final fallback = _tail(signal.note);
    if (rules.isEmpty && fallback.isEmpty) return const SizedBox.shrink();
    return InsetBox(
      padding: const EdgeInsets.fromLTRB(11, 10, 11, 11),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Сопровождение', color: C.faint),
          const SizedBox(height: 7),
          if (rules.isEmpty)
            Text(fallback, style: T.body(11.5, color: C.textSecondary, height: 1.5))
          else
            for (final rule in rules)
              Padding(
                padding: EdgeInsets.only(bottom: rule == rules.last ? 0 : 5),
                child: Text(
                  '· $rule',
                  style: T.body(11.5, color: C.textSecondary, height: 1.45),
                ),
              ),
          const SizedBox(height: 8),
          Text(
            'Увеличение риска после подтверждения запрещено.',
            style: T.body(11, color: C.faint, height: 1.4),
          ),
        ],
      ),
    );
  }

  static String _r(double v) => v.toStringAsFixed(1).replaceAll('.', ',');

  static String _tail(String note) {
    final end = note.indexOf('. ');
    return end < 0 ? '' : note.substring(end + 2);
  }
}

/// Можно ли исполнять этот план прямо сейчас.
class _ReadinessChip extends StatelessWidget {
  const _ReadinessChip({required this.state, required this.status});

  final IdeaState? state;
  final SignalStatus status;

  @override
  Widget build(BuildContext context) {
    final ({String label, Color color}) tone = switch (state) {
      IdeaState.triggered => (label: 'можно подтвердить', color: C.green),
      IdeaState.active => (label: 'в работе', color: C.info),
      IdeaState.ready => (label: 'ждём триггер', color: C.muted),
      IdeaState.watch => (label: 'не исполнять', color: C.muted),
      IdeaState.expired => (label: 'срок истёк', color: C.warning),
      IdeaState.missed => (label: 'рынок ушёл без нас', color: C.warning),
      IdeaState.invalidated => (label: 'замысел сломан', color: C.red),
      IdeaState.skipped => (label: 'пропущена', color: C.muted),
      IdeaState.closed => (label: 'закрыта', color: C.muted),
      null => status.canConfirm
          ? (label: 'можно подтвердить', color: C.green)
          : (label: 'не исполнять', color: C.muted),
    };
    return OutlineBadge(
      label: tone.label,
      color: tone.color,
      borderColor: tone.color.withValues(alpha: 0.35),
      background: tone.color.withValues(alpha: 0.12),
      fontWeight: 700,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      radius: R.pill,
    );
  }
}
