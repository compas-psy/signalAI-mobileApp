import 'package:flutter/widgets.dart';

import '../../data/api/equity_ranking_source.dart';
import '../../domain/research/equity_ranking.dart';
import '../../state/app_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Portfolio → Signals: a full daily ranking of Russian listed companies.
///
/// Rare early-signal hypotheses are an overlay on a company card, not the
/// admission ticket to the screen.  Therefore a calm research day still
/// contains the full ranked equity universe instead of an empty page.
class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key, required this.controller});

  final AppController controller;

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  final EquityRankingSource _source = EquityRankingSource();
  EquityRankingState? _ranking;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _load();
    // Keep the existing early-signal contour warm: the nightly server rank
    // already embeds its latest overlay, while this request preserves the
    // diagnostics/source state for the rest of the app.
    widget.controller.loadResearch();
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() => _loading = true);
    final result = await _source.load();
    if (!mounted) return;
    setState(() {
      _ranking = result;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final ranking = _ranking;
    if (ranking == null) {
      return _StateCard(
        title: 'Рейтинг компаний',
        text: _loading
            ? 'Загружаю ночной срез…'
            : 'Рейтинг ещё не запрошен.',
        busy: _loading,
        onRetry: _load,
      );
    }
    if (!ranking.isAvailable) {
      return _StateCard(
        title: 'Рейтинг недоступен',
        text: ranking.unavailableReason!,
        onRetry: _load,
      );
    }
    if (ranking.items.isEmpty) {
      return _StateCard(
        title: 'Рейтинг пока не собран',
        text: ranking.reason.isEmpty
            ? 'Движок ответил, но в инвестиционной вселенной пока нет компаний с данными.'
            : ranking.reason,
        onRetry: _load,
      );
    }

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _RankingHead(ranking: ranking, loading: _loading, onRefresh: _load),
        const SizedBox(height: 10),
        for (final item in ranking.items) ...[
          _CompanyCard(item: item),
          const SizedBox(height: 9),
        ],
      ],
    );
  }
}

class _RankingHead extends StatelessWidget {
  const _RankingHead({
    required this.ranking,
    required this.loading,
    required this.onRefresh,
  });

  final EquityRankingState ranking;
  final bool loading;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Рейтинг компаний')),
                Pressable(
                  onTap: loading ? null : onRefresh,
                  child: Text(
                    loading ? 'обновляю…' : 'обновить',
                    style: T.body(10.5, weight: 700, color: C.info),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 7),
            Text(
              'Сверху — компании, которые сейчас разумнее разбирать глубже. '
              'Внизу остаются слабые и недопущенные: они не исчезают, чтобы '
              'было видно весь рынок, а не только победителей.',
              style: T.body(11.5, color: C.textSecondary, height: 1.45),
            ),
            const SizedBox(height: 9),
            Wrap(
              spacing: 8,
              runSpacing: 5,
              children: [
                _MiniChip('${ranking.scoredCount} компаний'),
                _MiniChip('срез ${_day(ranking.marketDay)}'),
                if (ranking.dataAsOf != null)
                  _MiniChip('рынок до ${_dateTime(ranking.dataAsOf!)}'),
                const _MiniChip('пересчёт 1×/день'),
              ],
            ),
            const SizedBox(height: 9),
            Text(
              'Скоринг: 55% измеренный фундаментал + 45% D1-контекст. '
              'Подтверждённый ранний сигнал может добавить или снять до 10 баллов. '
              'Это очередь на изучение, не команда BUY/SELL.',
              style: T.body(10.5, color: C.faint, height: 1.45),
            ),
          ],
        ),
      );

  static String _day(String raw) {
    final d = DateTime.tryParse(raw);
    return d == null
        ? raw
        : '${d.day.toString().padLeft(2, '0')}.${d.month.toString().padLeft(2, '0')}';
  }

  static String _dateTime(DateTime value) {
    final local = value.toLocal();
    return '${local.day.toString().padLeft(2, '0')}.${local.month.toString().padLeft(2, '0')} '
        '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
  }
}

class _CompanyCard extends StatefulWidget {
  const _CompanyCard({required this.item});

  final EquityRankingItem item;

  @override
  State<_CompanyCard> createState() => _CompanyCardState();
}

class _CompanyCardState extends State<_CompanyCard> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final tone = _tone(item);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Pressable(
            onTap: () => setState(() => _open = !_open),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 31,
                  height: 31,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: C.inset,
                    border: Border.all(color: C.border),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: Text('#${item.rank}', style: T.mono(10.5, color: C.muted)),
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(item.symbol,
                          style: T.jost(15, weight: 700, color: C.text)),
                      if (item.title.isNotEmpty && item.title != item.symbol)
                        Text(
                          item.title,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: T.body(10.5, color: C.muted, height: 1.3),
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      item.score.toStringAsFixed(0),
                      style: T.mono(20, weight: 700, color: tone),
                    ),
                    Text('/100', style: T.mono(8.5, color: C.faint)),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              _TierChip(label: item.tier, color: tone),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  item.technicalState,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  textAlign: TextAlign.right,
                  style: T.body(10.5, color: C.textSecondary),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _ScoreCell('фундаментал', item.fundamentalScore),
              const SizedBox(width: 7),
              _ScoreCell('D1 техника', item.technicalScore),
              const SizedBox(width: 7),
              _DeltaCell(item.catalystAdjustment),
            ],
          ),
          if (item.hypothesis != null) ...[
            const SizedBox(height: 9),
            _HypothesisStrip(item: item),
          ],
          if (item.warnings.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              item.warnings.first,
              style: T.body(10.5, color: C.warning, height: 1.4),
            ),
          ],
          if (_open) ...[
            const SizedBox(height: 11),
            _Details(item: item),
          ] else ...[
            const SizedBox(height: 7),
            Align(
              alignment: Alignment.centerRight,
              child: Text('почему · раскрыть', style: T.body(9.5, color: C.faint)),
            ),
          ],
        ],
      ),
    );
  }

  static Color _tone(EquityRankingItem item) {
    if (!item.eligible) return C.red;
    if (item.score >= 75) return C.green;
    if (item.score >= 60) return C.accent;
    if (item.score >= 45) return C.info;
    return C.muted;
  }
}

class _Details extends StatelessWidget {
  const _Details({required this.item});

  final EquityRankingItem item;

  @override
  Widget build(BuildContext context) => InsetBox(
        padding: const EdgeInsets.fromLTRB(11, 10, 11, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Почему здесь', color: C.faint),
            const SizedBox(height: 6),
            if (item.fundamentalFacts.isEmpty)
              Text('Фундаментальные факты не измерены.',
                  style: T.body(10.5, color: C.faint))
            else
              for (final fact in item.fundamentalFacts)
                _Bullet(fact, color: C.textSecondary),
            if (item.technicalFacts.isNotEmpty) ...[
              const SizedBox(height: 7),
              const SectionLabel('D1 сейчас', color: C.faint),
              const SizedBox(height: 5),
              for (final fact in item.technicalFacts)
                _Bullet(fact, color: C.textSecondary),
            ],
            if (item.price != null) ...[
              const SizedBox(height: 7),
              Text(
                'последняя цена ${_price(item.price!)}'
                '${item.volatility3m == null ? '' : ' · волатильность 3м ${_pct(item.volatility3m!)}'}',
                style: T.mono(9.5, color: C.faint),
              ),
            ],
          ],
        ),
      );

  static String _pct(double value) => '${(value * 100).toStringAsFixed(1)}%';

  static String _price(double value) {
    final digits = value < 1 ? 4 : value < 100 ? 2 : 1;
    return value.toStringAsFixed(digits).replaceAll('.', ',');
  }
}

class _HypothesisStrip extends StatelessWidget {
  const _HypothesisStrip({required this.item});

  final EquityRankingItem item;

  @override
  Widget build(BuildContext context) {
    final hypothesis = item.hypothesis!;
    final color = hypothesis.positive
        ? C.green
        : hypothesis.negative
            ? C.red
            : C.info;
    final delta = item.catalystAdjustment;
    final deltaText = delta == 0
        ? '0'
        : '${delta > 0 ? '+' : '−'}${delta.abs().toStringAsFixed(1)}';
    return InsetBox(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('ранний сигнал', style: T.mono(9, color: color)),
                const SizedBox(height: 3),
                Text(
                  hypothesis.title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: T.body(10.5, color: C.textSecondary, height: 1.35),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Text(deltaText, style: T.mono(12, weight: 700, color: color)),
        ],
      ),
    );
  }
}

class _ScoreCell extends StatelessWidget {
  const _ScoreCell(this.label, this.value);

  final String label;
  final double value;

  @override
  Widget build(BuildContext context) => Expanded(
        child: InsetBox(
          padding: const EdgeInsets.fromLTRB(8, 7, 8, 7),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(value.toStringAsFixed(0),
                  style: T.mono(13, weight: 700, color: C.textSoft)),
              Text(label, style: T.body(8.8, color: C.faint)),
            ],
          ),
        ),
      );
}

class _DeltaCell extends StatelessWidget {
  const _DeltaCell(this.value);

  final double value;

  @override
  Widget build(BuildContext context) {
    final color = value > 0
        ? C.green
        : value < 0
            ? C.red
            : C.faint;
    final text = value == 0
        ? '0'
        : '${value > 0 ? '+' : '−'}${value.abs().toStringAsFixed(1)}';
    return Expanded(
      child: InsetBox(
        padding: const EdgeInsets.fromLTRB(8, 7, 8, 7),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(text, style: T.mono(13, weight: 700, color: color)),
            Text('ранний сигнал', style: T.body(8.8, color: C.faint)),
          ],
        ),
      ),
    );
  }
}

class _TierChip extends StatelessWidget {
  const _TierChip({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.10),
          border: Border.all(color: color.withValues(alpha: 0.35)),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(label, style: T.body(9.5, weight: 700, color: color)),
      );
}

class _MiniChip extends StatelessWidget {
  const _MiniChip(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: C.inset,
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.pill),
        ),
        child: Text(text, style: T.mono(9, color: C.muted)),
      );
}

class _Bullet extends StatelessWidget {
  const _Bullet(this.text, {required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 3),
        child: Text('· $text', style: T.body(10.5, color: color, height: 1.35)),
      );
}

class _StateCard extends StatelessWidget {
  const _StateCard({
    required this.title,
    required this.text,
    this.busy = false,
    this.onRetry,
  });

  final String title;
  final String text;
  final bool busy;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
        children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionLabel(title),
                const SizedBox(height: 7),
                Text(text, style: T.body(11.5, color: C.muted, height: 1.45)),
                if (busy) ...[
                  const SizedBox(height: 10),
                  const LinearProgressIndicator(minHeight: 2),
                ],
                if (onRetry != null && !busy) ...[
                  const SizedBox(height: 10),
                  Pressable(
                    onTap: onRetry,
                    child: Text('Повторить', style: T.body(11, weight: 700, color: C.info)),
                  ),
                ],
              ],
            ),
          ),
        ],
      );
}
