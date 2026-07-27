import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/settings.dart';
import '../../domain/models/signal.dart';
import '../../domain/position_sizing.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/trade_chart.dart';
import '../widgets/vector_icon.dart';

/// Карточка идеи: график, уровни, обоснование, смарт-риск и подтверждение.
class IdeaDetailScreen extends StatelessWidget {
  const IdeaDetailScreen({
    super.key,
    required this.signal,
    required this.risk,
    this.showBack = true,
  });

  final TradingSignal signal;
  final RiskProfile risk;

  /// Кнопка «назад» не нужна во второй колонке планшета: список идей никуда
  /// не пропадал, он слева.
  final bool showBack;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _DetailHeader(
          signal: signal,
          onBack: controller.back,
          showBack: showBack,
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(R.inner),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    border: Border.all(color: C.divider),
                    borderRadius: BorderRadius.circular(R.inner),
                  ),
                  child: TradeChart(signal: signal),
                ),
              ),
              const SizedBox(height: 12),
              _LevelsRow(signal: signal),
              const SizedBox(height: 12),
              _TakeProfitsCard(signal: signal),
              const SizedBox(height: 12),
              _ReasoningCard(signal: signal),
              const SizedBox(height: 12),
              _SmartRiskCard(signal: signal, risk: risk),
              const SizedBox(height: 12),
              _SignalEventsCard(events: signal.events),
              const SizedBox(height: 12),
              if (controller.paperAvailable) ...[
                _PaperCard(signal: signal),
                const SizedBox(height: 12),
              ],
              if (signal.status.canConfirm)
                Row(
                  children: [
                    Expanded(
                      child: Pressable(
                        pressedScale: .98,
                        onTap: controller.openSheet,
                        child: Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: C.accent,
                            borderRadius: BorderRadius.circular(R.button),
                          ),
                          child: Center(
                            child: Text(
                              'Отправить на биржу',
                              style: T.body(14, weight: 800, color: C.onAccent),
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 9),
                    Pressable(
                      onTap: controller.back,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                        decoration: BoxDecoration(
                          color: C.card,
                          border: Border.all(color: C.border),
                          borderRadius: BorderRadius.circular(R.button),
                        ),
                        child: Text('Пропустить', style: T.body(14, weight: 700, color: C.muted)),
                      ),
                    ),
                  ],
                )
              else
                Container(
                  padding: const EdgeInsets.all(13),
                  decoration: BoxDecoration(
                    color: C.greenFaint,
                    border: Border.all(color: const Color(0x4D2FD575)),
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Center(
                    child: Text(
                      'В работе · лимитный ордер и OCO (SL + ${signal.takeProfits.length} TP) выставлены',
                      textAlign: TextAlign.center,
                      style: T.body(13, weight: 700, color: C.green),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Ведение идеи на бумаге — отдельно от отправки на биржу.
///
/// Это разные вещи, и раньше их путала одна кнопка: «Подтвердить и
/// исполнить» упиралась в ключи, режим и подтверждение, а завести ту же идею
/// в журнал было нечем — при том что бумажному журналу не нужно ничего из
/// этого. Здесь видно, ведётся ли идея уже, и её можно завести одним нажатием.
class _PaperCard extends StatelessWidget {
  const _PaperCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final note = controller.paperNote(signal);
    return SectionCard(
      margin: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('На бумаге'),
          const SizedBox(height: 6),
          Text(
            note == null
                ? 'Идея в журнале не ведётся. Бумажная сделка проживается по '
                    'реальным свечам — без ключей, биржи и подтверждения: '
                    'именно она набирает выборку для допуска к живым деньгам.'
                : 'Ведётся в журнале: $note. Результат считается по реальным '
                    'свечам, уровни задним числом не правятся.',
            style: T.body(11.5, color: note == null ? C.muted : C.green, height: 1.5),
          ),
          if (note == null) ...[
            const SizedBox(height: 10),
            Pressable(
              onTap: controller.trackCurrentSignalOnPaper,
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 11),
                decoration: BoxDecoration(
                  border: Border.all(color: C.borderHover),
                  borderRadius: BorderRadius.circular(R.inner),
                ),
                child: Center(
                  child: Text(
                    'Вести на бумаге',
                    style: T.body(12.5, weight: 800, color: C.accent),
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _DetailHeader extends StatelessWidget {
  const _DetailHeader({
    required this.signal,
    required this.onBack,
    this.showBack = true,
  });

  final TradingSignal signal;
  final VoidCallback onBack;
  final bool showBack;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            if (showBack) ...[
              Pressable(
                onTap: onBack,
                child: Container(
                  width: 36,
                  height: 36,
                  decoration: BoxDecoration(
                    color: C.card,
                    shape: BoxShape.circle,
                    border: Border.all(color: C.border),
                  ),
                  child: const Center(
                    child: VectorIcon(Icons.chevronLeft, size: 18, color: C.text),
                  ),
                ),
              ),
              const SizedBox(width: 10),
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
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
                      const SizedBox(width: 8),
                      DirectionBadge(
                        label: signal.direction.label,
                        color: directionColor(signal.direction),
                        background: directionBackground(signal.direction),
                      ),
                    ],
                  ),
                  Text(
                    signal.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.body(11, color: C.muted),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(signal.lastPrice, style: T.mono(15, weight: 600)),
                Text(
                  signal.changeLabel,
                  style: T.mono(11, color: signal.changeUp ? C.green : C.red),
                ),
              ],
            ),
          ],
        ),
      );
}

class _LevelsRow extends StatelessWidget {
  const _LevelsRow({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          Expanded(
            child: _LevelCard(
              label: 'Вход · лимит',
              value: fmtPrice(signal.entry, signal.priceDecimals),
              color: C.accent,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _LevelCard(
              label: 'Стоп-лосс',
              value: fmtPrice(signal.stopLoss, signal.priceDecimals),
              color: C.red,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: _LevelCard(
              label: 'R:R до TP2',
              value: signal.riskReward,
              color: C.text,
            ),
          ),
        ],
      );
}

class _LevelCard extends StatelessWidget {
  const _LevelCard({required this.label, required this.value, required this.color});

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
        decoration: BoxDecoration(
          color: C.card,
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.inner),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label, style: T.body(10, color: C.muted)),
            const SizedBox(height: 2),
            Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: T.mono(14, weight: 600, color: color),
            ),
          ],
        ),
      );
}

class _TakeProfitsCard extends StatelessWidget {
  const _TakeProfitsCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Тейк-профиты'),
            for (final tp in signal.takeProfits) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  SizedBox(
                    width: 32,
                    child: Text(tp.label, style: T.mono(11, weight: 600, color: C.green)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      fmtPrice(tp.price, signal.priceDecimals),
                      style: T.mono(13, weight: 600),
                    ),
                  ),
                  Text('${tp.sharePercent}% объёма', style: T.body(11, color: C.muted)),
                  const SizedBox(width: 10),
                  Text(
                    PositionSizing.takeProfitR(signal, tp),
                    style: T.mono(11, color: C.green),
                  ),
                ],
              ),
            ],
          ],
        ),
      );
}

class _ReasoningCard extends StatelessWidget {
  const _ReasoningCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Почему это сделка')),
                Text(
                  'конфлюэнс ${signal.score}/100',
                  style: T.body(11, weight: 700, color: C.accent),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(signal.note, style: T.body(12, color: C.textSecondary, height: 1.55)),
            const SizedBox(height: 10),
            for (final factor in signal.factors) ...[
              _FactorRow(factor: factor),
              if (factor != signal.factors.last) const SizedBox(height: 9),
            ],
          ],
        ),
      );
}

class _FactorRow extends StatelessWidget {
  const _FactorRow({required this.factor});

  final SignalFactor factor;

  @override
  Widget build(BuildContext context) {
    // Вес фактора: 3 — сильный, 2 — средний, 1 — фоновый.
    final color = switch (factor.weight) {
      3 => C.accent,
      2 => const Color(0xFFB9A03E),
      _ => C.dim,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            Expanded(child: Text(factor.name, style: T.body(12, weight: 700))),
            const SizedBox(width: 8),
            Container(
              width: 52,
              height: 4,
              decoration: BoxDecoration(
                color: C.border,
                borderRadius: BorderRadius.circular(2),
              ),
              child: FractionallySizedBox(
                alignment: Alignment.centerLeft,
                widthFactor: (factor.fillPercent / 100).clamp(0, 1),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    color: color,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 2),
        Text(factor.text, style: T.body(11, color: C.muted, height: 1.45)),
      ],
    );
  }
}

/// Смарт-риск: сколько рублей на сделку и какой объём это даёт (ТЗ §6.2).
class _SmartRiskCard extends StatelessWidget {
  const _SmartRiskCard({required this.signal, required this.risk});

  final TradingSignal signal;
  final RiskProfile risk;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const VectorIcon(Icons.shield, size: 15, color: C.accent),
                const SizedBox(width: 8),
                const SectionLabel('Смарт-риск'),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: InsetBox(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Риск на сделку', style: T.body(10, color: C.muted)),
                        const SizedBox(height: 2),
                        Text('${fmt(risk.riskRub, 0)} ₽', style: T.mono(13, weight: 600)),
                        const SizedBox(height: 1),
                        Text(
                          '${riskPercentLabel(risk.riskPercent)} от ${fmt(risk.deposit, 0)} ₽',
                          style: T.body(10, color: C.muted),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: InsetBox(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Объём позиции', style: T.body(10, color: C.muted)),
                        const SizedBox(height: 2),
                        Text(
                          PositionSizing.quantityLabel(signal, risk),
                          style: T.mono(13, weight: 600, color: C.accent),
                        ),
                        const SizedBox(height: 1),
                        Text(signal.unitRiskLabel, style: T.body(10, color: C.muted)),
                      ],
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            // Потенциал сделки в деньгах: сколько принесут все тейки с их
            // долями и сколько заберёт стоп — при рассчитанном объёме.
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: InsetBox(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Потенциальная прибыль', style: T.body(10, color: C.muted)),
                        const SizedBox(height: 2),
                        Text(
                          '+${fmt(PositionSizing.potentialProfitRub(signal, risk), 0)} ₽',
                          style: T.mono(13, weight: 600, color: C.green),
                        ),
                        const SizedBox(height: 1),
                        Text('все тейки с долями объёма', style: T.body(10, color: C.muted)),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: InsetBox(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Потенциальный убыток', style: T.body(10, color: C.muted)),
                        const SizedBox(height: 2),
                        Text(
                          '−${fmt(PositionSizing.potentialLossRub(signal, risk), 0)} ₽',
                          style: T.mono(13, weight: 600, color: C.red),
                        ),
                        const SizedBox(height: 1),
                        Text('если сработает стоп-лосс', style: T.body(10, color: C.muted)),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
      );
}

class _SignalEventsCard extends StatelessWidget {
  const _SignalEventsCard({required this.events});

  final List<MarketEvent> events;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('События по идее'),
            for (final event in events) ...[
              const SizedBox(height: 9),
              Row(
                children: [
                  Container(
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      color: impactColor(event.impact),
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 10),
                  SizedBox(
                    width: 44,
                    child: Text(event.time, style: T.mono(11, color: C.muted)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(child: Text(event.text, style: T.body(12, color: C.textSoft))),
                ],
              ),
            ],
          ],
        ),
      );
}
