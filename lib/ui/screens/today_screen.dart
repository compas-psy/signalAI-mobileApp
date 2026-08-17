import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../data/ledger/capital_desk.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_funnel.dart';
import '../../domain/idea/paper_position.dart';
import '../../domain/idea/risk_center.dart';
import '../../domain/ledger/money.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import 'ideas_screen.dart';

/// Главная рабочая поверхность владельца.
///
/// Экран отвечает на пять вопросов без переходов: сколько денег, что уже в
/// позиции, что ждёт входа, где нужен человек и какие сетапы формируются.
/// Торговых решений здесь нет — это только проекция server-side lifecycle.
class TodayScreen extends StatefulWidget {
  const TodayScreen({super.key});

  @override
  State<TodayScreen> createState() => _TodayScreenState();
}

class _TodayScreenState extends State<TodayScreen> {
  bool _capitalSyncStarted = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_capitalSyncStarted) return;
    _capitalSyncStarted = true;

    // Cached ledger appears immediately; reconciliation with the real broker
    // accounts follows in the background. Entering Today therefore refreshes
    // the number the owner actually came to see instead of requiring
    // Portfolio → Accounts → Sync.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      AppScope.of(context).refreshCapital(sync: true);
    });
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final now = DateTime.now();
    final funnel = IdeaFunnelSnapshot.from(
      ideas: controller.ideas,
      trades: controller.paperPositions,
      now: now,
    );

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _CapitalSummary(controller: controller),
        const SizedBox(height: 12),
        _DaySummary(controller: controller),
        if (funnel.open.isNotEmpty) ...[
          const SizedBox(height: 18),
          _TradeBlock(
            title: 'Позиции открыты',
            note: 'Вход исполнен. Позиции сопровождает сервер.',
            trades: funnel.open,
            controller: controller,
          ),
        ],
        if (funnel.pending.isNotEmpty) ...[
          const SizedBox(height: 18),
          _TradeBlock(
            title: 'Ждут входа',
            note: 'Заявка уже выставлена, но цена до входа ещё не дошла.',
            trades: funnel.pending,
            controller: controller,
          ),
        ],
        if (funnel.decisions.isNotEmpty) ...[
          const SizedBox(height: 18),
          _IdeaBlock(
            title: 'Нужно решить',
            ideas: funnel.decisions,
            now: now,
            controller: controller,
            targetPill: IdeaFunnelPill.decisions,
          ),
        ],
        if (funnel.forming.isNotEmpty) ...[
          const SizedBox(height: 18),
          _IdeaBlock(
            title: 'Формируются',
            note: 'Кандидаты уже есть, но вход ещё не разрешён: ждём триггер.',
            ideas: funnel.forming.take(3).toList(),
            now: now,
            controller: controller,
            targetPill: IdeaFunnelPill.forming,
          ),
        ],
        if (funnel.total > 0) ...[
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerRight,
            child: Pressable(
              onTap: () {
                controller.goSection(AppSection.ideas);
                controller.goPill(IdeaFunnelPill.all.index);
              },
              child: Text(
                'Вся воронка · ${funnel.total} →',
                style: T.body(11.5, weight: 700, color: C.accent),
              ),
            ),
          ),
        ] else if (controller.ideasUnavailableReason != null) ...[
          const SizedBox(height: 18),
          SectionCard(
            child: Text(
              controller.ideasUnavailableReason!,
              style: T.body(11.5, color: C.warning, height: 1.5),
            ),
          ),
        ] else if (controller.noSetupsReason != null) ...[
          const SizedBox(height: 18),
          SectionCard(
            child: Text(
              controller.noSetupsReason!,
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
          ),
        ],
        if (controller.digest?.events.isNotEmpty ?? false) ...[
          const SizedBox(height: 18),
          _EventCard(controller: controller),
        ],
        if ((controller.capital?.packages.isNotEmpty ?? false)) ...[
          const SizedBox(height: 12),
          _PortfolioCard(controller: controller),
        ],
      ],
    );
  }
}

class _CapitalSummary extends StatelessWidget {
  const _CapitalSummary({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final state = controller.capital;
    return Pressable(
      onTap: () {
        controller.goSection(AppSection.portfolio);
        controller.goPill(PortfolioPill.accounts.index);
      },
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Капитал')),
                if (controller.capitalLoading)
                  Text('сверяем…', style: T.mono(10, color: C.info))
                else
                  Text('Счета →', style: T.body(10.5, color: C.accent, weight: 700)),
              ],
            ),
            const SizedBox(height: 9),
            if (state == null)
              Text(
                controller.capitalLoading
                    ? 'Сверяем Т‑Инвестиции и Bybit…'
                    : 'Книга капитала пока не загружена.',
                style: T.body(11.5, color: C.muted, height: 1.45),
              )
            else if (state.isEmpty)
              Text(
                controller.capitalLoading
                    ? 'Сверяем брокерские счета…'
                    : 'В книге пока нет брокерского снимка.',
                style: T.body(11.5, color: C.muted, height: 1.45),
              )
            else ...[
              _CapitalHeadline(state: state),
              const SizedBox(height: 10),
              for (final venue in _venueTotals(state).entries) ...[
                _MoneyRow(name: venue.key, values: venue.value),
                if (venue.key != _venueTotals(state).keys.last)
                  const SizedBox(height: 7),
              ],
              if (controller.capitalNote != null) ...[
                const SizedBox(height: 8),
                Text(
                  controller.capitalNote!,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: T.mono(9.5, color: C.faint, height: 1.35),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }

  static Map<String, Map<String, Money>> _venueTotals(CapitalState state) {
    final totals = <String, Map<String, Money>>{};

    String? venueOf(String accountId) {
      final id = accountId.toLowerCase();
      if (id.startsWith('tinvest')) return 'Т‑Инвестиции';
      if (id.startsWith('bybit')) return 'Bybit';
      return null;
    }

    void add(String venue, Money value) {
      final byCurrency = totals.putIfAbsent(venue, () => <String, Money>{});
      final current = byCurrency[value.currency.code];
      byCurrency[value.currency.code] = current == null ? value : current + value;
    }

    for (final account in state.accounts) {
      final venue = venueOf(account.id);
      if (venue == null) continue;
      for (final money in (state.snapshot.cash[account.id] ?? const <String, Money>{}).values) {
        add(venue, money);
      }
    }
    for (final position in state.snapshot.positions) {
      final venue = venueOf(position.accountId);
      if (venue == null) continue;
      final mark = state.marks[position.instrument];
      final value = mark == null
          ? position.costBasis.abs
          : mark.multiplyBy(position.quantity.abs);
      add(venue, value);
    }
    return totals;
  }
}

class _CapitalHeadline extends StatelessWidget {
  const _CapitalHeadline({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final hasForeignCash = state.snapshot.foreignCash().isNotEmpty;
    final hasForeignPosition = state.snapshot.positions.any((position) {
      final mark = state.marks[position.instrument];
      final currency = mark?.currency ?? position.costBasis.currency;
      return currency != state.base;
    });
    final complete = !hasForeignCash && !hasForeignPosition;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          fmtMoney(state.totalEquity),
          style: T.mono(20, weight: 700, color: C.text),
        ),
        const SizedBox(height: 2),
        Text(
          complete
              ? 'итого по брокерским счетам'
              : 'учтено в ${state.base.code}; другие валюты показаны ниже без выдуманного курса',
          style: T.body(10.5, color: C.muted, height: 1.35),
        ),
      ],
    );
  }
}

class _MoneyRow extends StatelessWidget {
  const _MoneyRow({required this.name, required this.values});

  final String name;
  final Map<String, Money> values;

  @override
  Widget build(BuildContext context) => Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(child: Text(name, style: T.body(11.5, color: C.textSoft))),
          const SizedBox(width: 10),
          Flexible(
            child: Text(
              values.isEmpty
                  ? '—'
                  : values.values.map(fmtMoney).join(' · '),
              textAlign: TextAlign.right,
              style: T.mono(11.5, weight: 600),
            ),
          ),
        ],
      );
}

class _DaySummary extends StatelessWidget {
  const _DaySummary({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final state = controller.capital;
    final deltas = state == null || state.isEmpty ? const [] : state.deltas();
    final delta = deltas.isEmpty ? null : deltas.first;
    final center = controller.riskCenter;
    final daily = center?.daily;

    if (delta == null && daily == null && center?.blocked != true) {
      return const SizedBox.shrink();
    }

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (delta != null) ...[
                Expanded(
                  child: _MiniMetric(
                    label: 'Результат дня',
                    value: fmtMoney(delta.amount, sign: true),
                    color: delta.amount.isNegative ? C.red : C.green,
                  ),
                ),
              ],
              if (delta != null && daily != null) const SizedBox(width: 18),
              if (daily != null)
                Expanded(
                  child: _MiniMetric(
                    label: 'Риск осталось',
                    value: _pct(daily.remainingPercent),
                    color: daily.exhausted ? C.red : C.accent,
                  ),
                ),
            ],
          ),
          if (daily != null) ...[
            const SizedBox(height: 10),
            LimitBar(usage: daily),
          ],
          if (center?.blocked == true) ...[
            const SizedBox(height: 8),
            Text(
              '${center!.tightest.name}: лимит исчерпан, новые входы заблокированы.',
              style: T.body(11, color: C.red, height: 1.4),
            ),
          ],
        ],
      ),
    );
  }

  static String _pct(double value) =>
      '${value.toStringAsFixed(2).replaceAll('.', ',')}%';
}

class _MiniMetric extends StatelessWidget {
  const _MiniMetric({required this.label, required this.value, required this.color});

  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: T.body(10.5, color: C.muted)),
          const SizedBox(height: 3),
          Text(value, style: T.mono(14, weight: 700, color: color)),
        ],
      );
}

class _TradeBlock extends StatelessWidget {
  const _TradeBlock({
    required this.title,
    required this.note,
    required this.trades,
    required this.controller,
  });

  final String title;
  final String note;
  final List<PaperPosition> trades;
  final AppController controller;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _BlockHead(
            title: '$title · ${trades.length}',
            note: note,
            onAll: () {
              controller.goSection(AppSection.ideas);
              controller.goPill(
                title == 'Ждут входа'
                    ? IdeaFunnelPill.pending.index
                    : IdeaFunnelPill.open.index,
              );
            },
          ),
          const SizedBox(height: 8),
          for (var index = 0; index < trades.length && index < 3; index++) ...[
            _CompactTradeCard(trade: trades[index], controller: controller),
            if (index + 1 < trades.length && index < 2) const SizedBox(height: 8),
          ],
        ],
      );
}

class _CompactTradeCard extends StatelessWidget {
  const _CompactTradeCard({required this.trade, required this.controller});

  final PaperPosition trade;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final pending = trade.status == PaperPositionStatus.pending;
    final decimals = trade.entry.abs() >= 1000 ? 2 : 4;
    final result = trade.resultR;
    return Pressable(
      onTap: trade.ideaId.isEmpty ? null : () => controller.openSignal(trade.ideaId),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text(trade.symbol, style: T.jost(16))),
                DirectionBadge(
                  label: trade.long ? 'LONG' : 'SHORT',
                  color: trade.long ? C.green : C.red,
                  background: (trade.long ? C.green : C.red).withValues(alpha: 0.12),
                ),
                if (result != null) ...[
                  const SizedBox(width: 7),
                  Text(
                    rMultiple(result),
                    style: T.mono(
                      11.5,
                      weight: 700,
                      color: result < 0 ? C.red : C.green,
                    ),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 7),
            Text(
              pending
                  ? 'заявка ${fmtPrice(trade.entry, decimals)} · цена ещё не дошла'
                  : 'вход ${fmtPrice(trade.entry, decimals)} · позиция открыта',
              style: T.body(11.5, color: C.textSoft),
            ),
            const SizedBox(height: 3),
            Text(
              'стоп ${fmtPrice(trade.currentStop, decimals)}'
              '${trade.tpsTotal > 0 ? ' · TP ${trade.tpsTaken}/${trade.tpsTotal}' : ''}',
              style: T.mono(10.5, color: C.muted),
            ),
          ],
        ),
      ),
    );
  }
}

class _IdeaBlock extends StatelessWidget {
  const _IdeaBlock({
    required this.title,
    required this.ideas,
    required this.now,
    required this.controller,
    required this.targetPill,
    this.note,
  });

  final String title;
  final String? note;
  final List<Idea> ideas;
  final DateTime now;
  final AppController controller;
  final IdeaFunnelPill targetPill;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _BlockHead(
            title: '$title · ${ideas.length}',
            note: note,
            onAll: () {
              controller.goSection(AppSection.ideas);
              controller.goPill(targetPill.index);
            },
          ),
          const SizedBox(height: 8),
          for (var index = 0; index < ideas.length; index++) ...[
            IdeaCard(
              idea: ideas[index],
              now: now,
              onTap: () => controller.openSignal(ideas[index].id),
            ),
            if (index + 1 < ideas.length) const SizedBox(height: 8),
          ],
        ],
      );
}

class _BlockHead extends StatelessWidget {
  const _BlockHead({required this.title, required this.onAll, this.note});

  final String title;
  final String? note;
  final VoidCallback onAll;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: SectionLabel(title)),
              Pressable(
                onTap: onAll,
                child: Text('Все →', style: T.body(10.5, color: C.accent, weight: 700)),
              ),
            ],
          ),
          if (note != null) ...[
            const SizedBox(height: 4),
            Text(note!, style: T.body(10.5, color: C.muted, height: 1.4)),
          ],
        ],
      );
}

class LimitBar extends StatelessWidget {
  const LimitBar({super.key, required this.usage});

  final LimitUsage usage;

  @override
  Widget build(BuildContext context) {
    final color = usage.exhausted
        ? C.red
        : usage.fill > 0.66
            ? C.warning
            : C.green;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                '${usage.name} · ${usage.window}',
                style: T.body(10.5, color: C.muted),
              ),
            ),
            Text(
              '${_pct(usage.usedPercent)} из ${_pct(usage.limitPercent)}',
              style: T.mono(10.5, color: color),
            ),
          ],
        ),
        const SizedBox(height: 5),
        ClipRRect(
          borderRadius: BorderRadius.circular(R.pill),
          child: SizedBox(
            height: 5,
            child: Stack(
              children: [
                const ColoredBox(color: C.inset, child: SizedBox.expand()),
                FractionallySizedBox(
                  widthFactor: usage.fill,
                  child: ColoredBox(color: color, child: const SizedBox.expand()),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  static String _pct(double value) =>
      '${value.toStringAsFixed(2).replaceAll('.', ',')}%';
}

class _EventCard extends StatelessWidget {
  const _EventCard({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final events = controller.digest?.events ?? const [];
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Событийный риск'),
          const SizedBox(height: 8),
          for (final event in events.take(2))
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 52,
                    child: Text(event.time, style: T.mono(11, color: C.muted)),
                  ),
                  Expanded(child: Text(event.text, style: T.body(12, height: 1.4))),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _PortfolioCard extends StatelessWidget {
  const _PortfolioCard({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final packages = controller.capital?.packages ?? const [];
    return Pressable(
      onTap: () => controller.goSection(AppSection.portfolio),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Портфель'),
            const SizedBox(height: 8),
            Text(
              'Пакетов в работе: ${packages.length}',
              style: T.body(12, color: C.text, height: 1.45),
            ),
          ],
        ),
      ),
    );
  }
}
