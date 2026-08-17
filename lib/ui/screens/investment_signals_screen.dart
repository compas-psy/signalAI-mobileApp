import 'package:flutter/widgets.dart';

import '../../data/api/equity_ranking_source.dart';
import '../../domain/research/equity_ranking.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Portfolio → Signals now exposes the complete server-ranked Russian-equity
/// universe. The phone presents the snapshot; it never recalculates ranking.
class InvestmentSignalsScreen extends StatefulWidget {
  const InvestmentSignalsScreen({super.key, EquityRankingSource? source})
      : source = source ?? const _DefaultEquityRankingSource();

  final EquityRankingSource source;

  @override
  State<InvestmentSignalsScreen> createState() => _InvestmentSignalsScreenState();
}

/// Const wrapper keeps the default widget constructor cheap while retaining an
/// injectable source for deterministic owner-UX tests.
class _DefaultEquityRankingSource extends EquityRankingSource {
  const _DefaultEquityRankingSource();

  @override
  Future<EquityRankingState> load() => EquityRankingSource().load();
}

enum _RadarFilter { all, early, watch, late }

class _InvestmentSignalsScreenState extends State<InvestmentSignalsScreen> {
  EquityRankingState? _state;
  bool _loading = false;
  _RadarFilter _filter = _RadarFilter.all;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() => _loading = true);
    final next = await widget.source.load();
    if (!mounted) return;
    setState(() {
      _state = next;
      _loading = false;
    });
  }

  List<EquityRankingItem> _visible(EquityRankingState state) => switch (_filter) {
        _RadarFilter.all => state.items,
        _RadarFilter.early => state.items.where((item) => item.isEarly).toList(),
        _RadarFilter.watch => state.items
            .where((item) => !item.isEarly && !item.isLate)
            .toList(),
        _RadarFilter.late => state.items.where((item) => item.isLate).toList(),
      };

  @override
  Widget build(BuildContext context) {
    final state = _state;
    final visible = state == null ? const <EquityRankingItem>[] : _visible(state);
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 96),
      children: [
        _Intro(
          state: state,
          visibleCount: visible.length,
          loading: _loading,
          onRefresh: _load,
        ),
        const SizedBox(height: 10),
        if (state != null && state.isAvailable && state.items.isNotEmpty) ...[
          _Filters(
            value: _filter,
            onChanged: (value) => setState(() => _filter = value),
          ),
          const SizedBox(height: 10),
        ],
        if (state == null)
          const SectionCard(
            child: BusyLine(label: 'Загружаем ранний радар российских акций…'),
          )
        else if (!state.isAvailable)
          _Unavailable(state: state, onRefresh: _load)
        else if (state.items.isEmpty)
          _EmptyState(state: state, onRefresh: _load)
        else if (visible.isEmpty)
          SectionCard(
            child: Text(
              'В этой группе сейчас нет бумаг. Переключи фильтр на «Все», чтобы вернуть полную вселенную.',
              style: T.body(11.5, color: C.textSecondary, height: 1.5),
            ),
          )
        else ...[
          for (final item in visible) ...[
            _RadarCard(item: item),
            const SizedBox(height: 9),
          ],
        ],
      ],
    );
  }
}

class _Intro extends StatelessWidget {
  const _Intro({
    required this.state,
    required this.visibleCount,
    required this.loading,
    required this.onRefresh,
  });

  final EquityRankingState? state;
  final int visibleCount;
  final bool loading;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final asOf = state?.dataAsOf?.toLocal();
    final freshness = asOf == null
        ? 'AS-OF —'
        : 'AS-OF ${asOf.day.toString().padLeft(2, '0')}.${asOf.month.toString().padLeft(2, '0')}';
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Ранний радар акций')),
              if (state != null && state!.isAvailable)
                OutlineBadge(
                  label: freshness,
                  color: C.green,
                  borderColor: C.greenBorder,
                ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            'Вся российская фондовая вселенная в серверном рейтинге. Наверху — бумаги, '
            'где движение ещё может быть ранним; поздние и слабые не скрываются, а '
            'остаются ниже с объяснением. Это исследовательский радар, не торговый ордер.',
            style: T.body(11.5, color: C.textSecondary, height: 1.5),
          ),
          if (state != null && state!.isAvailable) ...[
            const SizedBox(height: 9),
            Text(
              'Показано $visibleCount из ${state!.items.length} · вселенная ${state!.universeCount} · '
              'оценено ${state!.scoredCount} · ${state!.methodology}',
              style: T.mono(10, color: C.faint),
            ),
          ],
          const SizedBox(height: 10),
          ActionButton(
            label: loading ? 'Обновляем…' : 'Обновить радар',
            dense: true,
            onTap: loading ? null : onRefresh,
          ),
        ],
      ),
    );
  }
}

class _Filters extends StatelessWidget {
  const _Filters({required this.value, required this.onChanged});

  final _RadarFilter value;
  final ValueChanged<_RadarFilter> onChanged;

  @override
  Widget build(BuildContext context) => SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            _FilterChip('Все', _RadarFilter.all, value, onChanged),
            const SizedBox(width: 6),
            _FilterChip('Ранние', _RadarFilter.early, value, onChanged),
            const SizedBox(width: 6),
            _FilterChip('Наблюдать', _RadarFilter.watch, value, onChanged),
            const SizedBox(width: 6),
            _FilterChip('Поздно', _RadarFilter.late, value, onChanged),
          ],
        ),
      );
}

class _FilterChip extends StatelessWidget {
  const _FilterChip(this.label, this.filter, this.value, this.onChanged);

  final String label;
  final _RadarFilter filter;
  final _RadarFilter value;
  final ValueChanged<_RadarFilter> onChanged;

  @override
  Widget build(BuildContext context) {
    final selected = filter == value;
    return Pressable(
      onTap: () => onChanged(filter),
      child: OutlineBadge(
        label: label,
        color: selected ? C.accent : C.textSecondary,
        borderColor: selected ? C.accent : C.border,
        background: selected ? C.accent.withValues(alpha: 0.08) : C.card,
        fontWeight: selected ? 800 : 500,
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 6),
      ),
    );
  }
}

class _Unavailable extends StatelessWidget {
  const _Unavailable({required this.state, required this.onRefresh});

  final EquityRankingState state;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Радар недоступен', color: C.red),
            const SizedBox(height: 7),
            Text(
              state.unavailableReason ?? 'Сервер не вернул рейтинг.',
              style: T.body(11.5, color: C.textSecondary, height: 1.5),
            ),
            const SizedBox(height: 10),
            ActionButton(label: 'Повторить', dense: true, onTap: onRefresh),
          ],
        ),
      );
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.state, required this.onRefresh});

  final EquityRankingState state;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Вселенная пока пуста', color: C.warning),
            const SizedBox(height: 7),
            Text(
              state.reason.isEmpty
                  ? 'Сервер отработал, но не вернул ни одной оценённой акции.'
                  : state.reason,
              style: T.body(11.5, color: C.textSecondary, height: 1.5),
            ),
            const SizedBox(height: 8),
            Text(
              'Вселенная ${state.universeCount} · оценено ${state.scoredCount}.',
              style: T.mono(10, color: C.faint),
            ),
            const SizedBox(height: 10),
            ActionButton(label: 'Переспросить сервер', dense: true, onTap: onRefresh),
          ],
        ),
      );
}

class _RadarCard extends StatefulWidget {
  const _RadarCard({required this.item});

  final EquityRankingItem item;

  @override
  State<_RadarCard> createState() => _RadarCardState();
}

class _RadarCardState extends State<_RadarCard> {
  bool expanded = false;

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final stateColor = item.isLate
        ? C.warning
        : item.isEarly
            ? C.green
            : C.accent;
    return Pressable(
      onTap: () => setState(() => expanded = !expanded),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 34,
                  child: Text('#${item.rank}', style: T.mono(11, color: C.faint)),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Text(item.symbol, style: T.jost(18)),
                          const SizedBox(width: 7),
                          if (item.rankChange != null && item.rankChange != 0)
                            Text(
                              item.rankChange! > 0 ? '↑${item.rankChange}' : '↓${item.rankChange!.abs()}',
                              style: T.mono(
                                10,
                                color: item.rankChange! > 0 ? C.green : C.warning,
                                weight: 700,
                              ),
                            ),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(item.title, style: T.body(10.5, color: C.muted)),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text('${item.score.toStringAsFixed(0)}/100', style: T.mono(15, weight: 700)),
                    const SizedBox(height: 3),
                    Text(
                      item.earlyScore == null ? 'early —' : 'early ${item.earlyScore!.toStringAsFixed(0)}',
                      style: T.mono(9.5, color: stateColor, weight: 700),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: Text(
                    item.earlyState.isEmpty ? 'раннее состояние не измерено' : item.earlyState,
                    style: T.body(10.8, color: stateColor, weight: 700),
                  ),
                ),
                Text(expanded ? 'СВЕРНУТЬ' : 'РАСКРЫТЬ', style: T.mono(8.5, color: C.faint)),
              ],
            ),
            if (expanded) ...[
              const SizedBox(height: 12),
              _EvidenceSection(item: item),
            ],
          ],
        ),
      ),
    );
  }
}

class _EvidenceSection extends StatelessWidget {
  const _EvidenceSection({required this.item});

  final EquityRankingItem item;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Почему сейчас'),
          const SizedBox(height: 5),
          if (item.whyNow.isEmpty)
            Text('Ранних измеримых преимуществ пока нет.', style: T.body(11, color: C.textSecondary))
          else
            for (final fact in item.whyNow)
              _Bullet(fact, color: item.isLate ? C.warning : C.textSecondary),
          const SizedBox(height: 10),
          const SectionLabel('Качество идеи'),
          const SizedBox(height: 6),
          InsetBox(
            child: Row(
              children: [
                Expanded(child: _Metric('Фундаментал', '${item.fundamentalScore.toStringAsFixed(0)}/100')),
                Expanded(child: _Metric('D1 техника', '${item.technicalScore.toStringAsFixed(0)}/100')),
                Expanded(child: _Metric('Early', _score(item.earlyScore))),
              ],
            ),
          ),
          if (item.fundamentalFacts.isNotEmpty || item.technicalFacts.isNotEmpty) ...[
            const SizedBox(height: 7),
            for (final fact in item.fundamentalFacts.take(3)) _Bullet(fact),
            for (final fact in item.technicalFacts.take(3)) _Bullet(fact),
          ],
          if (item.hypothesis != null) ...[
            const SizedBox(height: 7),
            _Bullet('Катализатор: ${item.hypothesis!.title}', color: C.accent),
          ],
          const SizedBox(height: 10),
          const SectionLabel('Подтверждение'),
          const SizedBox(height: 4),
          Text(item.confirmation.isEmpty ? '—' : item.confirmation, style: T.body(11, color: C.textSecondary, height: 1.45)),
          const SizedBox(height: 10),
          const SectionLabel('Инвалидация'),
          const SizedBox(height: 4),
          Text(item.invalidation.isEmpty ? '—' : item.invalidation, style: T.body(11, color: C.textSecondary, height: 1.45)),
          const SizedBox(height: 10),
          const SectionLabel('Динамика'),
          const SizedBox(height: 6),
          InsetBox(
            child: Wrap(
              spacing: 16,
              runSpacing: 9,
              children: [
                _CompactMetric('5 дней', _pct(item.return5d)),
                _CompactMetric('20 дней', _pct(item.return20d)),
                _CompactMetric('3 месяца', _pct(item.return3m)),
                _CompactMetric('6 месяцев', _pct(item.return6m)),
                _CompactMetric('До breakout', _pct(item.breakoutDistance)),
                _CompactMetric('Оборот 5/20', _ratio(item.turnoverRatio)),
                _CompactMetric('Накопление', _pct(item.accumulationScore)),
                _CompactMetric('Сжатие vol', _ratio(item.compressionRatio)),
                _CompactMetric('Anti-chase', _score01(item.chasePenalty)),
              ],
            ),
          ),
          if (item.warnings.isNotEmpty || item.isLate) ...[
            const SizedBox(height: 10),
            const SectionLabel('Предупреждения', color: C.warning),
            const SizedBox(height: 5),
            if (item.isLate) const _Bullet('Позднее растяжение: не догонять движение.', color: C.warning),
            for (final warning in item.warnings) _Bullet(warning, color: C.warning),
          ],
        ],
      );

  static String _score(double? value) => value == null ? '—' : '${value.toStringAsFixed(0)}/100';
  static String _pct(double? value) => value == null ? '—' : '${value >= 0 ? '+' : ''}${(value * 100).toStringAsFixed(1)}%';
  static String _ratio(double? value) => value == null ? '—' : '${value.toStringAsFixed(2)}×';
  static String _score01(double? value) => value == null ? '—' : '${(value * 100).toStringAsFixed(0)}%';
}

class _Bullet extends StatelessWidget {
  const _Bullet(this.text, {this.color = C.textSecondary});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 4),
        child: Text('· $text', style: T.body(10.8, color: color, height: 1.4)),
      );
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: T.body(9.2, color: C.faint)),
          const SizedBox(height: 2),
          Text(value, style: T.mono(11, weight: 700)),
        ],
      );
}

class _CompactMetric extends StatelessWidget {
  const _CompactMetric(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => SizedBox(
        width: 112,
        child: Text('$label · $value', style: T.mono(9.5, color: C.textSecondary)),
      );
}
