import 'package:flutter/widgets.dart';

import '../../data/api/portfolio_headlines_client.dart';
import '../../domain/portfolio/headline.dart';
import '../../domain/portfolio/package.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/engine_package_widgets.dart';

/// Основной owner-facing экран инвестиционных пакетов.
///
/// Внутренние SIMPLE/BALANCED/MAX_POTENTIAL не показываются как выбор:
/// сервер сам выбирает один headline на профиль. Клиент только показывает
/// три решения и точный горизонт, на котором они были рассчитаны.
class PortfolioHeadlinesScreen extends StatefulWidget {
  const PortfolioHeadlinesScreen({
    super.key,
    required this.horizonYears,
    this.client,
  });

  final int horizonYears;
  final PortfolioHeadlinesClient? client;

  @override
  State<PortfolioHeadlinesScreen> createState() => _PortfolioHeadlinesScreenState();
}

class _PortfolioHeadlinesScreenState extends State<PortfolioHeadlinesScreen> {
  late final PortfolioHeadlinesClient _client =
      widget.client ?? PortfolioHeadlinesClient();
  late Future<PortfolioHeadlines> _future = _load();

  Future<PortfolioHeadlines> _load() =>
      _client.fetch(horizonYears: widget.horizonYears);

  @override
  void didUpdateWidget(covariant PortfolioHeadlinesScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.horizonYears != widget.horizonYears) {
      _future = _load();
    }
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<PortfolioHeadlines>(
        future: _future,
        builder: (context, snapshot) {
          if (!snapshot.hasData) {
            return const Padding(
              padding: EdgeInsets.fromLTRB(S.screen, 18, S.screen, 90),
              child: BusyLine(label: 'Сервер выбирает три актуальных портфеля…'),
            );
          }
          final state = snapshot.data!;
          if (!state.isAvailable) {
            return ListView(
              padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
              children: [
                SectionCard(
                  child: Text(
                    state.unavailableReason ?? 'Портфели временно недоступны',
                    style: T.body(12, color: C.muted, height: 1.45),
                  ),
                ),
              ],
            );
          }
          return ListView(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
            children: [
              Text(
                'Три стратегии · горизонт ${state.horizonYears == 1 ? '1 год' : '${state.horizonYears}+ лет'}',
                style: T.body(11.5, color: C.faint),
              ),
              const SizedBox(height: 10),
              for (var i = 0; i < state.portfolios.length; i++) ...[
                _HeadlineCard(headline: state.portfolios[i]),
                if (i != state.portfolios.length - 1) const SizedBox(height: 10),
              ],
            ],
          );
        },
      );
}

class _HeadlineCard extends StatelessWidget {
  const _HeadlineCard({required this.headline});

  final PortfolioHeadline headline;

  @override
  Widget build(BuildContext context) {
    final package = headline.package;
    final status = switch (headline.status) {
      PortfolioHeadlineStatus.ready => ('Готов', C.green),
      PortfolioHeadlineStatus.riskierThanTarget => ('Риск выше профиля', C.warning),
      PortfolioHeadlineStatus.missing => ('Нет состава', C.muted),
    };
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(headline.label, style: T.jost(19, weight: 600))),
              OutlineBadge(
                label: status.$1,
                color: status.$2,
                borderColor: status.$2,
              ),
            ],
          ),
          if (headline.reason.isNotEmpty) ...[
            const SizedBox(height: 7),
            Text(headline.reason, style: T.body(11.5, color: C.muted, height: 1.4)),
          ],
          if (package != null) ...[
            const SizedBox(height: 14),
            const SectionLabel('Ожидаемая доходность'),
            const SizedBox(height: 5),
            ExpectedBand(low: package.expectedLow, high: package.expectedHigh),
            const SizedBox(height: 13),
            KeyValueRow(
              name: 'Целевая волатильность',
              value: sharePercent(package.volatility, decimals: 1),
            ),
            KeyValueRow(
              name: 'Лимит просадки модели',
              value: sharePercent(package.drawdown, decimals: 1),
            ),
            if (package.cvar95 != null)
              KeyValueRow(
                name: 'CVaR 95%',
                value: sharePercent(package.cvar95!, decimals: 1),
                showDivider: false,
              ),
            if (package.stress.isNotEmpty) ...[
              const SizedBox(height: 12),
              const SectionLabel('Стресс'),
              const SizedBox(height: 6),
              StressTiles(stress: package.stress),
            ],
            if (package.mix.isNotEmpty) ...[
              const SizedBox(height: 14),
              const SectionLabel('Состав'),
              const SizedBox(height: 7),
              MixBar(mix: package.mix),
              const SizedBox(height: 8),
              MixLegend(mix: package.mix),
            ],
            if (package.positions.isNotEmpty) ...[
              const SizedBox(height: 14),
              const SectionLabel('Позиции и доказательства'),
              const SizedBox(height: 5),
              for (final position in package.positions)
                _EvidencePosition(
                  position: position,
                  evidence: headline.evidenceByInstrument[position.instrumentId],
                  maxWeight: package.positions
                      .map((item) => item.weight)
                      .fold<double>(0, (max, value) => value > max ? value : max),
                ),
            ],
            const SizedBox(height: 14),
            _Changes(changes: headline.changes),
            const SizedBox(height: 12),
            InsetBox(
              child: Text(
                'Ребалансировка — только рекомендация. Приложение не отправляет инвестиционные заявки автоматически.',
                style: T.body(11, color: C.faint, height: 1.45),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _EvidencePosition extends StatelessWidget {
  const _EvidencePosition({
    required this.position,
    required this.evidence,
    required this.maxWeight,
  });

  final PackagePosition position;
  final Map<String, dynamic>? evidence;
  final double maxWeight;

  @override
  Widget build(BuildContext context) {
    final summary = '${evidence?['summary'] ?? ''}';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        PositionRow(position: position, maxWeight: maxWeight),
        if (summary.isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(left: 72, bottom: 6),
            child: Text(
              summary,
              style: T.body(10.5, color: C.textSecondary, height: 1.35),
            ),
          ),
      ],
    );
  }
}

class _Changes extends StatelessWidget {
  const _Changes({required this.changes});

  final PortfolioModelChanges changes;

  @override
  Widget build(BuildContext context) {
    final parts = <String>[
      if (changes.added.isNotEmpty) '+${changes.added.length}',
      if (changes.removed.isNotEmpty) '−${changes.removed.length}',
      if (changes.weightChanged.isNotEmpty) 'Δ${changes.weightChanged.length}',
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionLabel('Изменения модели'),
        const SizedBox(height: 5),
        Text(
          parts.isEmpty ? 'С прошлого расчёта материальных изменений нет' : parts.join(' · '),
          style: T.body(11.5, color: parts.isEmpty ? C.faint : C.textSoft),
        ),
      ],
    );
  }
}
