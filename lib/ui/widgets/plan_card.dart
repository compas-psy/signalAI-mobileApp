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
///
/// Прототип (`plan-card`) держит их вместе не для экономии места. Решение
/// «входить или нет» принимается по всем трём сразу: цена входа без объёма и
/// объём без правила переноса стопа — это не план, а фрагменты плана.
///
/// Раньше то же самое было разложено по трём карточкам внутри сегмента
/// «План»: тейк-профиты, смарт-риск и план ведения — три прокрутки вместо
/// одного взгляда.
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
        // Вход бывает лимитным (ретест зоны) и стоповым (пробой) — подпись
        // обязана это различать, иначе ордер уйдёт не тот.
        label: plan == null
            ? (signal.entryIsStop ? 'Вход · стоп' : 'Вход · лимит')
            : 'Вход · ${plan.orderType.label.toLowerCase()}',
        // Зона входа двумя числами в одну плитку не помещается и обрезается
        // многоточием — а обрезанная цена хуже отсутствующей. Нижняя граница
        // это цена заявки, верхняя — предел, за которым сделка уже другая;
        // вторая уходит подписью и остаётся на экране целиком.
        value: fmtPrice(plan?.entryLow ?? signal.entry, decimals),
        color: C.accent,
        hint: plan == null ? null : 'до ${fmtPrice(plan.entryHigh, decimals)}',
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
            : '${fmt(plan.quantity, 0)} ${signal.unitName}',
        color: C.accent,
        hint: signal.unitRiskLabel,
      ),
      MetricTile(
        label: 'R:R',
        value: plan == null ? signal.riskReward : _r(plan.rrToSecondTarget),
        hint: 'до TP2',
      ),
      // Потенциал сделки в деньгах: сколько принесут все тейки с их долями и
      // сколько заберёт стоп — при рассчитанном объёме.
      //
      // Когда план есть, обе цифры считаются по нему, а не по профилю риска.
      // Иначе на одной карточке стояло «риск 2 920 ₽» и «убыток по стопу
      // 17 600 ₽»: первое — из плана движка, второе — из процента депозита,
      // и они относятся к разным объёмам. Одна карточка, два ответа на один
      // вопрос — это не мелочь оформления, это потеря доверия к числам.
      MetricTile(
        label: 'Прибыль',
        value: '+${fmt(plan == null ? PositionSizing.potentialProfitRub(signal, risk) : plan.weightedR * plan.riskRubles, 0)} ₽',
        color: C.green,
        hint: 'все тейки с долями',
      ),
      MetricTile(
        label: 'Убыток',
        value: '−${fmt(plan?.riskRubles ?? PositionSizing.potentialLossRub(signal, risk), 0)} ₽',
        color: C.red,
        hint: 'если сработает стоп',
      ),
    ];
  }

  /// Сопровождение — правила, которые подписываются вместе с входом.
  ///
  /// Берутся из плана движка, если он есть: «перенести стоп в безубыток» без
  /// записанного правила остаётся обещанием на словах.
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

  /// Хвост обоснования: всё, кроме первого предложения. Первое — вывод, оно
  /// стоит в тезисе.
  static String _tail(String note) {
    final end = note.indexOf('. ');
    return end < 0 ? '' : note.substring(end + 2);
  }
}

/// Можно ли исполнять этот план прямо сейчас (прототип: чип «можно
/// подтвердить» / «не исполнять» в шапке карточки).
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
