import 'package:flutter/widgets.dart';

import '../../data/api/investment_signal_source.dart';
import '../../domain/research/investment_signal.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Portfolio → Signals is an action shortlist, not a renamed market ranking.
class InvestmentSignalsScreen extends StatefulWidget {
  const InvestmentSignalsScreen({super.key});

  @override
  State<InvestmentSignalsScreen> createState() => _InvestmentSignalsScreenState();
}

class _InvestmentSignalsScreenState extends State<InvestmentSignalsScreen> {
  final InvestmentSignalSource _source = InvestmentSignalSource();
  InvestmentSignalsState? _state;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() => _loading = true);
    final next = await _source.load();
    if (!mounted) return;
    setState(() {
      _state = next;
      _loading = false;
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = _state;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 96),
      children: [
        _Intro(state: state, loading: _loading, onRefresh: _load),
        const SizedBox(height: 12),
        if (state == null)
          const SectionCard(
            child: BusyLine(label: 'Собираем фундаментал + D1 технику + катализаторы…'),
          )
        else if (state.failed)
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Сигналы не получены', color: C.red),
                const SizedBox(height: 7),
                Text(state.error, style: T.body(11.5, color: C.textSecondary, height: 1.5)),
                const SizedBox(height: 10),
                ActionButton(label: 'Повторить', dense: true, onTap: _load),
              ],
            ),
          )
        else if (state.signals.isEmpty)
          _EmptyState(state: state, onRefresh: _load)
        else ...[
          for (final signal in state.signals) ...[
            _SignalCard(signal: signal),
            const SizedBox(height: 10),
          ],
        ],
      ],
    );
  }
}

class _Intro extends StatelessWidget {
  const _Intro({required this.state, required this.loading, required this.onRefresh});

  final InvestmentSignalsState? state;
  final bool loading;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) {
    final s = state;
    final day = s?.dataAsOf?.toLocal();
    final freshness = day == null
        ? 'дата рынка пока не подтверждена'
        : 'данные ${day.day.toString().padLeft(2, '0')}.${day.month.toString().padLeft(2, '0')}';
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Инвестиционные сигналы')),
              if (s != null && !s.failed)
                OutlineBadge(
                  label: freshness.toUpperCase(),
                  color: C.green,
                  borderColor: C.greenBorder,
                ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            'Российские акции · горизонт 6–12 месяцев. Сигнал появляется только '
            'из измеренного фундаментала, D1-техники и, когда есть, раннего '
            'катализатора. Это не краткосрочный ордер: точку входа и стоп здесь '
            'не выдумываем без отдельного инвестиционного execution-модуля.',
            style: T.body(11.5, color: C.textSecondary, height: 1.5),
          ),
          if (s != null && !s.failed) ...[
            const SizedBox(height: 9),
            Text(
              'Вселенная ${s.universeCount} · оценено ${s.scoredCount} · '
              'пригодно ${s.eligibleCount} · сигналов ${s.signals.length}',
              style: T.mono(10, color: C.faint),
            ),
          ],
          const SizedBox(height: 10),
          ActionButton(
            label: loading ? 'Обновляем…' : 'Обновить сигналы',
            dense: true,
            onTap: loading ? null : onRefresh,
          ),
        ],
      ),
    );
  }
}

class _EmptyState extends StatelessWidget {
  const _EmptyState({required this.state, required this.onRefresh});

  final InvestmentSignalsState state;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Сегодня без инвестиционного входа', color: C.warning),
            const SizedBox(height: 7),
            Text(
              state.reason.isEmpty
                  ? 'Движок отработал, но ни одна акция не проходит инвестиционный допуск.'
                  : state.reason,
              style: T.body(11.5, color: C.textSecondary, height: 1.5),
            ),
            const SizedBox(height: 8),
            Text(
              'Это теперь диагностируемая пустота, а не пустой экран: '
              'вселенная ${state.universeCount}, оценено ${state.scoredCount}, '
              'пригодно ${state.eligibleCount}.',
              style: T.mono(10, color: C.faint, height: 1.45),
            ),
            const SizedBox(height: 10),
            ActionButton(label: 'Переспросить сервер', dense: true, onTap: onRefresh),
          ],
        ),
      );
}

class _SignalCard extends StatelessWidget {
  const _SignalCard({required this.signal});

  final InvestmentSignal signal;

  @override
  Widget build(BuildContext context) {
    final color = switch (signal.action) {
      'ACCUMULATE' => C.green,
      'EARLY' => C.accent,
      _ => C.info,
    };
    final price = signal.price == null
        ? 'цена —'
        : '${signal.price!.toStringAsFixed(signal.price! >= 100 ? 1 : 2).replaceAll('.', ',')} ₽';
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(signal.symbol, style: T.jost(20)),
                        const SizedBox(width: 7),
                        OutlineBadge(
                          label: signal.actionLabel,
                          color: color,
                          borderColor: color.withValues(alpha: 0.35),
                          background: color.withValues(alpha: 0.08),
                          fontWeight: 800,
                        ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(signal.title, style: T.body(11, color: C.muted)),
                  ],
                ),
              ),
              Text(
                '${signal.score.toStringAsFixed(0)}/100',
                style: T.mono(17, weight: 700, color: color),
              ),
            ],
          ),
          const SizedBox(height: 10),
          InsetBox(
            child: Row(
              children: [
                Expanded(child: _Metric('Сейчас', price)),
                Expanded(
                  child: _Metric(
                    'Фундаментал',
                    '${signal.fundamentalScore.toStringAsFixed(0)}/100',
                  ),
                ),
                Expanded(
                  child: _Metric(
                    'Техника D1',
                    '${signal.technicalScore.toStringAsFixed(0)}/100',
                  ),
                ),
              ],
            ),
          ),
          if (signal.technicalState.isNotEmpty) ...[
            const SizedBox(height: 7),
            Text(
              '${signal.horizon} · ${signal.technicalState}',
              style: T.mono(10, color: C.faint),
            ),
          ],
          if (signal.why.isNotEmpty) ...[
            const SizedBox(height: 11),
            const SectionLabel('Почему сейчас', color: C.faint),
            const SizedBox(height: 5),
            for (final fact in signal.why.take(5))
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text('· $fact', style: T.body(11.5, color: C.textSecondary, height: 1.4)),
              ),
          ],
          if (signal.risks.isNotEmpty) ...[
            const SizedBox(height: 8),
            const SectionLabel('Что мешает', color: C.warning),
            const SizedBox(height: 5),
            for (final risk in signal.risks.take(3))
              Padding(
                padding: const EdgeInsets.only(bottom: 3),
                child: Text('· $risk', style: T.body(10.8, color: C.muted, height: 1.4)),
              ),
          ],
        ],
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label, style: T.body(9.5, color: C.faint)),
          const SizedBox(height: 2),
          Text(value, style: T.mono(11.5, weight: 700)),
        ],
      );
}
