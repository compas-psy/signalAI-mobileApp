import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/settings.dart';
import '../../domain/models/signal.dart';
import '../../domain/position_sizing.dart';
import '../../domain/risk/portfolio_impact.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import 'common.dart';
import 'vector_icon.dart';

/// Шит подтверждения сделки.
///
/// Последний рубеж контроля (ТЗ §7): ничего не исполняется без явного
/// подтверждения. После нажатия сервер выставляет лимитку и OCO-связку.
class ConfirmSheet extends StatelessWidget {
  const ConfirmSheet({
    super.key,
    required this.signal,
    required this.risk,
    required this.onExecute,
    this.onRiskBoost,
    this.plan,
    this.impact,
    required this.onClose,
    required this.busy,
    this.paperOnly = false,
  });

  final TradingSignal signal;

  /// План движка. Подписывается именно он: объём и риск, посчитанные на
  /// телефоне из профиля, разошлись бы с теми, что показала карточка идеи, —
  /// и владелец подтвердил бы не ту сделку, которую видел.
  final TradePlan? plan;

  final RiskProfile risk;
  final VoidCallback onExecute;

  /// Отдельный owner-action для server-owned manual risk preview/apply.
  ///
  /// Он намеренно не связан с [onExecute]: повышение риска фиксирует override,
  /// но само по себе не создаёт paper-сделку и не отправляет ордер.
  final VoidCallback? onRiskBoost;

  /// Что сделка сделает с портфелем: открытый риск, число сделок, корреляция.
  /// null — считать не из чего (нет книги и профиля риска).
  final PortfolioImpact? impact;
  final VoidCallback onClose;
  final bool busy;

  /// Серверная идея первой поставки исполняется только на бумаге. Подписи
  /// обязаны сказать это до последнего тапа, а не после создания позиции.
  final bool paperOnly;

  @override
  Widget build(BuildContext context) {
    final decimals = signal.priceDecimals;
    final shares = signal.takeProfits.map((tp) => '${tp.sharePercent}').join('/');
    final rows = <_SheetRow>[
      _SheetRow('Вход · лимит', fmtPrice(signal.entry, decimals), C.accent),
      _SheetRow(
        'Объём (риск ${riskPercentLabel(plan?.riskPercent ?? risk.riskPercent)})',
        plan == null
            ? PositionSizing.quantityLabel(signal, risk)
            : _planQuantity(plan!),
        C.text,
      ),
      _SheetRow('Стоп-лосс', fmtPrice(signal.stopLoss, decimals), C.red),
      _SheetRow(
        signal.takeProfits.map((tp) => tp.label).join(' / '),
        signal.takeProfits.map((tp) => fmtPrice(tp.price, decimals)).join(' / '),
        C.green,
      ),
      _SheetRow(
        'Риск, если SL',
        '−${fmt(plan?.riskRubles ?? risk.riskRub, 0)} ₽',
        C.red,
      ),
      _SheetRow('R:R до TP2', signal.riskReward, C.text),
    ];

    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onClose,
      child: ColoredBox(
        color: const Color(0x99000000),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            GestureDetector(
              onTap: () {}, // клик по самому шиту не закрывает его
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(18, 16, 18, 22),
                decoration: const BoxDecoration(
                  color: C.sheet,
                  border: Border(top: BorderSide(color: C.borderStrong)),
                  borderRadius: BorderRadius.vertical(top: Radius.circular(R.sheet)),
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Center(
                      child: Container(
                        width: 36,
                        height: 4,
                        margin: const EdgeInsets.only(bottom: 14),
                        decoration: BoxDecoration(
                          color: C.handle,
                          borderRadius: BorderRadius.circular(2),
                        ),
                      ),
                    ),
                    Row(
                      children: [
                        Flexible(
                          child: Text(
                            signal.symbol,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: T.jost(18),
                          ),
                        ),
                        const SizedBox(width: 9),
                        DirectionBadge(
                          label: signal.direction.label,
                          color: directionColor(signal.direction),
                          background: directionBackground(signal.direction),
                        ),
                        const Spacer(),
                        Text(
                          paperOnly ? 'paper · сервер' : 'лимитный ордер',
                          style: T.body(11, color: C.muted),
                        ),
                      ],
                    ),
                    const SizedBox(height: 13),
                    InsetBox(
                      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 4),
                      radius: R.inner,
                      child: Column(
                        children: [
                          for (final row in rows)
                            Container(
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              decoration: const BoxDecoration(
                                border: Border(bottom: BorderSide(color: C.divider)),
                              ),
                              child: Row(
                                children: [
                                  Expanded(
                                    child: Text(row.name, style: T.body(12, color: C.muted)),
                                  ),
                                  const SizedBox(width: 12),
                                  Flexible(
                                    child: Text(
                                      row.value,
                                      textAlign: TextAlign.right,
                                      style: T.mono(12, weight: 600, color: row.color),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                        ],
                      ),
                    ),
                    if (impact != null) ...[
                      const SizedBox(height: 12),
                      _RiskChecks(impact: impact!),
                    ],
                    const SizedBox(height: 12),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
                      decoration: BoxDecoration(
                        color: const Color(0x0FFFD400),
                        border: Border.all(color: const Color(0x33FFD400)),
                        borderRadius: BorderRadius.circular(R.inset),
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const VectorIcon(Icons.info, size: 14, color: C.accent),
                          const SizedBox(width: 8),
                          Expanded(
                            child: Text(
                              // План целиком до входа — подтверждать сделку
                              // можно только понимая, как она будет вестись
                              // и что её сломает.
                              '${paperOnly ? 'В paper-сделку' : 'На биржу'} уйдут лимитка ${fmtPrice(signal.entry, decimals)}, '
                              'стоп ${fmtPrice(signal.stopLoss, decimals)} и цели '
                              '($shares%). Ведение: после TP1 стоп остатка — в '
                              'безубыток; выход по времени через 5 торговых дней, '
                              'если цели не достигнуты. Идея ломается закреплением '
                              'за стопом.',
                              style: T.body(11, color: C.textSecondary, height: 1.45),
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (paperOnly && onRiskBoost != null) ...[
                      const SizedBox(height: 12),
                      ActionButton(
                        label: 'Рискнуть',
                        onTap: busy ? null : onRiskBoost,
                      ),
                    ],
                    const SizedBox(height: 14),
                    Row(
                      children: [
                        Expanded(
                          child: Pressable(
                            pressedScale: .98,
                            onTap: busy ? null : onExecute,
                            child: Container(
                              padding: const EdgeInsets.all(14),
                              decoration: BoxDecoration(
                                color: busy ? C.chip : C.accent,
                                borderRadius: BorderRadius.circular(R.button),
                              ),
                              child: Center(
                                child: Text(
                                  busy
                                      ? 'Подтверждаем…'
                                      : (paperOnly
                                          ? 'Подтвердить paper-сделку'
                                          : 'Исполнить на бирже'),
                                  style: T.body(
                                    14,
                                    weight: 800,
                                    color: busy ? C.muted : C.onAccent,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 9),
                        Pressable(
                          onTap: onClose,
                          child: Container(
                            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                            decoration: BoxDecoration(
                              color: C.inset,
                              border: Border.all(color: C.border),
                              borderRadius: BorderRadius.circular(R.button),
                            ),
                            child: Text(
                              'Отмена',
                              style: T.body(14, weight: 700, color: C.muted),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  /// Объём плана в номинале инструмента.
  ///
  /// Дробность обязательна: 0,348 ETH, округлённые до целых, стали бы нулём,
  /// а «28» без единицы измерения — двадцатью восемью монетами.
  static String _planQuantity(TradePlan plan) {
    final value = fmt(plan.quantity, plan.quantityDecimals);
    return plan.quantityUnit.isEmpty ? value : '$value ${plan.quantityUnit}';
  }
}

class _SheetRow {
  const _SheetRow(this.name, this.value, this.color);

  final String name;
  final String value;
  final Color color;
}


/// Риск-проверки перед отправкой: что сделка сделает с портфелем.
///
/// Показываются всегда, а не только при нарушении: увидеть «открытый риск
/// станет 4,2% из 6%» до отправки — это и есть управление капиталом, а
/// разбираться постфактум поздно.
class _RiskChecks extends StatelessWidget {
  const _RiskChecks({required this.impact});

  final PortfolioImpact impact;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 11),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Влияние на портфель'),
            const SizedBox(height: 8),
            for (final check in impact.checks) ...[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    margin: const EdgeInsets.only(top: 5),
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      color: switch (check.verdict) {
                        RiskVerdict.ok => C.green,
                        RiskVerdict.warning => C.warning,
                        RiskVerdict.blocking => C.red,
                      },
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 9),
                  Expanded(
                    child: Text.rich(
                      TextSpan(
                        text: '${check.name}: ',
                        style: T.body(11.5, weight: 700),
                        children: [
                          TextSpan(
                            text: check.detail,
                            style: T.body(11.5, weight: 400, color: C.muted),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 7),
            ],
            Text(
              'Позиция появится в книге только после подтверждения исполнения '
              'брокером — заявка сама по себе капитал не меняет.',
              style: T.body(10, color: C.faint, height: 1.4),
            ),
          ],
        ),
      );
}