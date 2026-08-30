import 'dart:async';

import 'package:flutter/widgets.dart';

import '../../data/api/control_dashboard_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';

typedef ControlDashboardLoader = Future<ControlDashboardSnapshot> Function(
  String venue,
);

/// Owner-facing read-only control plane for strategy evidence.
///
/// This screen never changes strategy roles, thresholds, risk or execution.
/// It only reads the server-owned measurement snapshot and keeps missing
/// evidence missing instead of turning it into reassuring zeroes.
class ServerControlScreen extends StatefulWidget {
  const ServerControlScreen({super.key, this.loader});

  /// Test/offline seam. Production uses [ControlDashboardClient].
  final ControlDashboardLoader? loader;

  @override
  State<ServerControlScreen> createState() => _ServerControlScreenState();
}

class _ServerControlScreenState extends State<ServerControlScreen> {
  late final ControlDashboardClient _client = ControlDashboardClient();
  int _venue = 1; // BYBIT first: the owner is currently diagnosing crypto flow.
  int _requestGeneration = 0;
  bool _loading = true;
  ControlDashboardSnapshot? _snapshot;
  String? _error;

  String get _venueName => _venue == 0 ? 'FORTS' : 'BYBIT';

  @override
  void initState() {
    super.initState();
    unawaited(_load());
  }

  @override
  void didUpdateWidget(covariant ServerControlScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.loader != widget.loader) unawaited(_load());
  }

  Future<void> _load() async {
    final generation = ++_requestGeneration;
    final venue = _venueName;
    if (mounted) {
      setState(() {
        _loading = true;
        _error = null;
      });
    }
    try {
      final loader = widget.loader;
      final value = loader == null
          ? await _client.load(venue: venue)
          : await loader(venue);
      if (!mounted || generation != _requestGeneration) return;
      if (value.venue != venue) {
        throw FormatException(
          'Control dashboard venue mismatch: requested $venue, got ${value.venue}',
        );
      }
      setState(() {
        _snapshot = value;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || generation != _requestGeneration) return;
      setState(() {
        _snapshot = null;
        _loading = false;
        _error = error.toString();
      });
    }
  }

  void _selectVenue(int index) {
    if (index == _venue) return;
    setState(() {
      _venue = index;
      _snapshot = null;
      _error = null;
    });
    unawaited(_load());
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = _snapshot;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 28),
      children: [
        _header(snapshot),
        const SizedBox(height: 12),
        if (_loading && snapshot == null)
          const SectionCard(
            child: BusyLine(label: 'Читаем серверный snapshot контроля…'),
          )
        else if (_error != null)
          _ErrorCard(message: _error!, onRetry: _load)
        else if (snapshot != null) ...[
          _runtime(snapshot),
          if (snapshot.venue == 'BYBIT' && snapshot.funnel.scan != null) ...[
            const SizedBox(height: 12),
            _scanFunnel(snapshot.funnel.scan!),
          ],
          const SizedBox(height: 12),
          _competition(snapshot),
          if (snapshot.venue == 'BYBIT' && snapshot.dataReadiness != null) ...[
            const SizedBox(height: 12),
            _dataReadiness(snapshot.dataReadiness!),
          ],
          const SizedBox(height: 12),
          _backtest(snapshot.backtest),
          const SizedBox(height: 12),
          _riskOptimizer(snapshot.riskOptimizer),
        ],
      ],
    );
  }

  Widget _header(ControlDashboardSnapshot? snapshot) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(child: Text('Контроль стратегий', style: T.jost(20))),
                if (snapshot != null)
                  OutlineBadge(
                    label: _healthLabel(snapshot.health),
                    color: _statusColor(snapshot.health),
                    borderColor: _statusBorder(snapshot.health),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'read-only · показывает факты live scan, конкуренции, исторических данных, бэктеста и risk optimizer; ничего не промоутит и не меняет риск.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
            const SizedBox(height: 14),
            SegmentedControl(
              items: const ['FORTS', 'BYBIT'],
              index: _venue,
              onSelect: _selectVenue,
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: Text(
                    snapshot == null
                        ? 'Окно 7 дней'
                        : 'Окно ${snapshot.windowHours ~/ 24} дней · snapshot ${_shortTime(snapshot.generatedAt)}',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.body(10.5, color: C.dim),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 104,
                  child: ActionButton(
                    label: 'Обновить',
                    onTap: _loading ? null : () => unawaited(_load()),
                    dense: true,
                  ),
                ),
              ],
            ),
          ],
        ),
      );

  Widget _runtime(ControlDashboardSnapshot snapshot) {
    final control = snapshot.funnel.control;
    final roles = snapshot.runtimeRoles;
    final liveVersion = roles?.liveGenerator.version ??
        snapshot.competition.controlVersion;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Сейчас'),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: Text(
                  liveVersion,
                  style: T.mono(12.5, weight: 700, color: C.text),
                ),
              ),
              if (roles != null)
                const OutlineBadge(
                  label: 'LIVE GENERATOR',
                  color: C.green,
                  borderColor: C.greenBorder,
                ),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            roles == null
                ? 'Production scanner · создано ${control.ideasCreated} · показано ${control.presented}'
                : '${roles.liveGenerator.publishesTradeIdeas ? 'Создаёт TradeIdea' : 'Не создаёт TradeIdea'} · создано ${control.ideasCreated} · показано ${control.presented}',
            style: T.body(11.5, color: C.textSecondary, height: 1.45),
          ),
          if (roles != null && roles.liveGenerator.strategyFamilies.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              'Live families: ${roles.liveGenerator.strategyFamilies.join(' · ')}',
              style: T.mono(9.8, color: C.dim, height: 1.4),
            ),
          ],
          if (roles != null && roles.shadowOnly.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              'SHADOW ONLY · ${roles.shadowOnly.join(' · ')}',
              style: T.mono(10.5, weight: 700, color: C.warning),
            ),
            const SizedBox(height: 3),
            Text(
              roles.governanceControlsRuntime
                  ? 'Governance registry управляет runtime.'
                  : 'Champion/challenger — измерение, а не переключатель live generation.',
              style: T.body(10.4, color: C.muted, height: 1.4),
            ),
          ],
          if (control.statuses.isNotEmpty) ...[
            const SizedBox(height: 7),
            Text(
              'Статусы: ${_counts(control.statuses)}',
              style: T.body(10.3, color: C.dim, height: 1.4),
            ),
          ],
          if (control.qualities.isNotEmpty) ...[
            const SizedBox(height: 3),
            Text(
              'Quality: ${_counts(control.qualities)}',
              style: T.body(10.3, color: C.dim, height: 1.4),
            ),
          ],
          if (snapshot.competition.candidates.isEmpty) ...[
            const SizedBox(height: 10),
            Text(
              'Новых кандидатов в этом окне пока нет.',
              style: T.body(11.5, color: C.muted, height: 1.45),
            ),
          ],
          for (final candidate in snapshot.competition.candidates) ...[
            const SizedBox(height: 10),
            _CandidateRuntimeCard(candidate: candidate),
          ],
        ],
      ),
    );
  }

  Widget _scanFunnel(BybitScanFunnel funnel) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Bybit funnel'),
            const SizedBox(height: 7),
            Text(
              '${funnel.universe} → ${funnel.dataHealthy} → ${funnel.liquid} → ${funnel.strategyEvaluated} · published ${funnel.published}',
              style: T.mono(12, weight: 700, color: C.text),
            ),
            const SizedBox(height: 3),
            Text(
              'universe → data healthy → liquid → strategy evaluated',
              style: T.body(9.8, color: C.dim),
            ),
            const SizedBox(height: 7),
            Text(
              'Regime eligible ${funnel.regimeEligible} · setup reject ${funnel.setupReject} · cost/RR reject ${funnel.costRrReject}',
              style: T.body(10.6, color: C.textSecondary, height: 1.4),
            ),
            if (funnel.topReasons.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                funnel.topReasons
                    .map((item) => '${item.reason} · ${item.count}')
                    .join('\n'),
                style: T.mono(9.8, color: C.warning, height: 1.5),
              ),
            ],
          ],
        ),
      );

  Widget _competition(ControlDashboardSnapshot snapshot) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Конкуренция'),
            const SizedBox(height: 5),
            Text(
              'CONTROL ${snapshot.competition.controlVersion} · минимум сравнимой выборки ${snapshot.competition.minComparableSample}',
              style: T.body(10.8, color: C.muted, height: 1.45),
            ),
            if (snapshot.runtimeRoles?.champion != null) ...[
              const SizedBox(height: 5),
              Text(
                'Measurement champion: ${snapshot.runtimeRoles!.champion} · не означает live publication',
                style: T.body(10.5, color: C.info, height: 1.4),
              ),
            ],
            if (snapshot.competition.candidates.isEmpty) ...[
              const SizedBox(height: 10),
              Text(
                'Paper A/B ещё не накопил кандидатов.',
                style: T.body(11.5, color: C.muted),
              ),
            ],
            for (final candidate in snapshot.competition.candidates) ...[
              const SizedBox(height: 10),
              _CompetitionCard(
                candidate: candidate,
                requiredPairs: snapshot.competition.minComparableSample,
              ),
            ],
          ],
        ),
      );

  Widget _dataReadiness(BybitDataReadiness readiness) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Historical data')),
                OutlineBadge(
                  label: readiness.status.replaceAll('_', ' '),
                  color: readiness.status == 'DATA_READY' ? C.green : C.warning,
                  borderColor: readiness.status == 'DATA_READY'
                      ? C.greenBorder
                      : C.warningBorder,
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'Immutable multi-stream snapshots · 36m gate проверяется по каждому обязательному потоку.',
              style: T.body(10.6, color: C.muted, height: 1.45),
            ),
            if (readiness.symbols.isEmpty) ...[
              const SizedBox(height: 8),
              Text(
                'Пока нет опубликованных Bybit research datasets.',
                style: T.body(11.2, color: C.muted),
              ),
            ],
            for (final symbol in readiness.symbols) ...[
              const SizedBox(height: 9),
              InsetBox(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            symbol.symbol,
                            style: T.mono(11.2, weight: 700, color: C.text),
                          ),
                        ),
                        OutlineBadge(
                          label: symbol.status.replaceAll('_', ' '),
                          color: symbol.status == 'DATA_READY'
                              ? C.green
                              : C.warning,
                          borderColor: symbol.status == 'DATA_READY'
                              ? C.greenBorder
                              : C.warningBorder,
                        ),
                      ],
                    ),
                    const SizedBox(height: 5),
                    Text(
                      'rows ${symbol.rowCount} · snapshot ${_shortHash(symbol.snapshotId)}',
                      style: T.mono(9.7, color: C.dim),
                    ),
                    for (final item in symbol.coverage.where((item) => !item.ready)) ...[
                      const SizedBox(height: 4),
                      Text(
                        '${item.stream} · ${item.reason}',
                        style: T.mono(9.8, color: C.warning),
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ],
        ),
      );

  Widget _backtest(ControlBacktestSnapshot snapshot) {
    final run = snapshot.latest;
    final byStrategy = snapshot.byStrategy;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Бэктест / OOS'),
          const SizedBox(height: 8),
          if (byStrategy.isNotEmpty) ...[
            Text(
              'Strategy OOS · последний immutable evidence-run по каждой стратегии',
              style: T.body(10.8, color: C.muted, height: 1.45),
            ),
            for (final strategyRun in byStrategy) ...[
              const SizedBox(height: 8),
              _StrategyBacktestCard(run: strategyRun),
            ],
          ] else if (run == null)
            Text(
              'Для $_venueName нет сохранённого серверного бэктеста.',
              style: T.body(11.5, color: C.muted, height: 1.45),
            )
          else ...[
            Row(
              children: [
                Expanded(
                  child: Text(
                    run.label,
                    style: T.mono(12.5, weight: 700, color: C.text),
                  ),
                ),
                OutlineBadge(
                  label: run.gatePassed ? 'GATE PASS' : 'GATE BLOCK',
                  color: run.gatePassed ? C.green : C.warning,
                  borderColor:
                      run.gatePassed ? C.greenBorder : C.warningBorder,
                ),
              ],
            ),
            const SizedBox(height: 7),
            Text(
              '${run.periodFrom} → ${run.periodTo} · N ${run.trades}',
              style: T.body(10.8, color: C.muted),
            ),
            const SizedBox(height: 8),
            Text(
              'PF ${_n(run.profitFactor)} · expectancy ${_r(run.expectancyR)} · MaxDD ${_n(run.maxDrawdown)}',
              style: T.mono(11.5, color: C.textSecondary),
            ),
            const SizedBox(height: 5),
            Text(
              'Sharpe ${_n(run.sharpe)} · Sortino ${_n(run.sortino)} · Brier ${_n(run.brierScore)} · PBO ${_n(run.pbo)}',
              style: T.mono(10.8, color: C.muted),
            ),
            const SizedBox(height: 8),
            Text(
              'config ${_shortHash(run.configHash)} · engine ${run.engineVersion}',
              style: T.mono(10, color: C.dim),
            ),
          ],
          const SizedBox(height: 10),
          Text(
            'Walk-forward: ${snapshot.walkForward['train_months'] ?? '—'}m train · ${snapshot.walkForward['validation_months'] ?? '—'}m validation · ${snapshot.walkForward['test_months'] ?? '—'}m OOS',
            style: T.body(10.8, color: C.muted, height: 1.45),
          ),
          const SizedBox(height: 5),
          Text(
            'Paper gate: N ≥ ${snapshot.paperGate['min_aggregate_trades'] ?? '—'} · setup ≥ ${snapshot.paperGate['min_trades_per_setup'] ?? '—'} · PF ≥ ${_configN(snapshot.paperGate['min_oos_profit_factor'])} · expectancy ≥ ${_configR(snapshot.paperGate['min_oos_expectancy_r'])}',
            style: T.body(10.5, color: C.muted, height: 1.45),
          ),
          const SizedBox(height: 3),
          Text(
            'Live gate: N ≥ ${snapshot.liveGate['min_paper_trades'] ?? '—'} · days ≥ ${snapshot.liveGate['min_paper_days'] ?? '—'}',
            style: T.body(10.5, color: C.muted, height: 1.45),
          ),
        ],
      ),
    );
  }

  Widget _riskOptimizer(RiskOptimizerSnapshot snapshot) {
    final champion = snapshot.champion;
    final run = snapshot.latestRun;
    final cfg = snapshot.config;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Risk optimizer'),
          const SizedBox(height: 8),
          if (champion == null)
            Text(
              'Champion risk-policy ещё не выбран.',
              style: T.body(11.5, color: C.muted),
            )
          else ...[
            Row(
              children: [
                Expanded(
                  child: Text(
                    champion.candidateId ?? champion.version,
                    style: T.mono(12.5, weight: 700, color: C.text),
                  ),
                ),
                const OutlineBadge(
                  label: 'CHAMPION',
                  color: C.info,
                  borderColor: C.infoBorder,
                ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              '${champion.sampleSize} / ${cfg.minSamples} sample · cadence ${cfg.cadenceDays}d · gate +${_r(cfg.minOosExpectancyImprovementR)}',
              style: T.body(10.8, color: C.textSecondary, height: 1.45),
            ),
          ],
          const SizedBox(height: 7),
          Text(
            'Кандидаты: ${cfg.candidateIds.isEmpty ? '—' : cfg.candidateIds.join(' · ')}',
            style: T.body(10.8, color: C.muted, height: 1.45),
          ),
          if (run != null) ...[
            const SizedBox(height: 8),
            Text(
              'Последний прогон: ${run.label} · ${run.gatePassed ? 'gate pass' : 'gate block'} · expectancy ${_r(run.expectancyR)} · MaxDD ${_n(run.maxDrawdown)} · top5 ${_n(run.top5Contribution)}',
              style: T.body(10.8, color: C.muted, height: 1.45),
            ),
          ],
          if (snapshot.nextDueAt != null) ...[
            const SizedBox(height: 5),
            Text(
              'Следующая проверка не раньше ${_shortTime(snapshot.nextDueAt!)}',
              style: T.body(10.3, color: C.dim),
            ),
          ],
          const SizedBox(height: 8),
          Text(
            cfg.absoluteRiskCapsMutable
                ? 'ВНИМАНИЕ: сервер сообщает изменяемые hard caps.'
                : 'Безопасность: hard caps неизменны; optimizer меняет только bounded exit geometry.',
            style: T.body(
              10.8,
              color: cfg.absoluteRiskCapsMutable ? C.red : C.muted,
              height: 1.45,
            ),
          ),
        ],
      ),
    );
  }
}

class _StrategyBacktestCard extends StatelessWidget {
  const _StrategyBacktestCard({required this.run});

  final BacktestRunSummary run;

  @override
  Widget build(BuildContext context) {
    final reason = run.gateDetail['reason']?.toString();
    final metricSpace = run.report['metric_space']?.toString();
    final isR = metricSpace == 'R_MULTIPLES';
    final status = run.gatePassed
        ? 'OOS PASS'
        : reason != null && reason.isNotEmpty
            ? 'BLOCKED'
            : 'GATE FAIL';
    final color = run.gatePassed ? C.green : C.warning;
    final border = run.gatePassed ? C.greenBorder : C.warningBorder;
    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  run.strategy ?? run.label,
                  style: T.mono(11.2, weight: 700, color: C.text),
                ),
              ),
              OutlineBadge(label: status, color: color, borderColor: border),
            ],
          ),
          const SizedBox(height: 5),
          if (run.trades > 0)
            Text(
              'N ${run.trades} · E[R] ${_signedR(run.expectancyR)} · PF ${_dotN(run.profitFactor)} · MaxDD ${isR ? _rMagnitude(run.maxDrawdown) : _dotN(run.maxDrawdown)}',
              style: T.mono(10.5, color: C.textSecondary),
            )
          else
            Text(
              'N 0 · historical outcome не вычислялся',
              style: T.body(10.4, color: C.muted),
            ),
          if (reason != null && reason.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              reason,
              style: T.mono(9.7, color: C.warning, height: 1.4),
            ),
          ],
          if (isR) ...[
            const SizedBox(height: 4),
            Text(
              'R_MULTIPLES · account return не моделируется',
              style: T.body(9.6, color: C.dim),
            ),
          ],
        ],
      ),
    );
  }
}

class _CandidateRuntimeCard extends StatelessWidget {
  const _CandidateRuntimeCard({required this.candidate});

  final CompetitionCandidateSummary candidate;

  @override
  Widget build(BuildContext context) {
    final shadow = candidate.shadow;
    final reasons = shadow.topUnavailableReasons;
    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  candidate.version,
                  style: T.mono(11.5, weight: 700, color: C.text),
                ),
              ),
              OutlineBadge(
                label: _verdictLabel(candidate.verdict),
                color: _statusColor(candidate.verdict),
                borderColor: _statusBorder(candidate.verdict),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            '${shadow.evaluated} / ${shadow.observations} обработано · signals ${shadow.emitted} · unavailable ${shadow.unavailable}',
            style: T.body(10.8, color: C.textSecondary),
          ),
          if (reasons.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text(
              reasons
                  .map((item) => '${item.reason} · ${item.count}')
                  .join('\n'),
              style: T.mono(9.8, color: C.warning, height: 1.45),
            ),
          ],
        ],
      ),
    );
  }
}

class _CompetitionCard extends StatelessWidget {
  const _CompetitionCard({
    required this.candidate,
    required this.requiredPairs,
  });

  final CompetitionCandidateSummary candidate;
  final int requiredPairs;

  @override
  Widget build(BuildContext context) {
    final paper = candidate.paper;
    final comparable = paper.comparablePairs;
    final remaining = requiredPairs > comparable ? requiredPairs - comparable : 0;
    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  candidate.version,
                  style: T.mono(11.5, weight: 700, color: C.text),
                ),
              ),
              Text(
                _verdictLabel(candidate.verdict),
                style: T.microLabel(color: _statusColor(candidate.verdict)),
              ),
            ],
          ),
          const SizedBox(height: 6),
          Text(
            'N $comparable/$requiredPairs · осталось $remaining',
            style: T.mono(10.8, color: C.textSecondary),
          ),
          const SizedBox(height: 4),
          if (comparable == 0)
            Text(
              'Сравнимых исходов пока нет · control: ${_r(paper.controlMeanNetR)} · кандидат: ${_r(paper.candidateMeanNetR)}',
              style: T.body(10.8, color: C.muted, height: 1.45),
            )
          else
            Text(
              'control ${_r(paper.controlMeanNetR)} · кандидат ${_r(paper.candidateMeanNetR)} · Δ ${_r(paper.deltaMeanNetR)}',
              style: T.mono(10.8, color: C.textSecondary),
            ),
          const SizedBox(height: 4),
          Text(
            'Outcomes: control ${paper.control.evaluatedOutcomes}/${paper.control.decisions} · candidate ${paper.candidate.evaluatedOutcomes}/${paper.candidate.decisions}',
            style: T.body(10, color: C.dim),
          ),
        ],
      ),
    );
  }
}

class _ErrorCard extends StatelessWidget {
  const _ErrorCard({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Данные недоступны', color: C.warning),
            const SizedBox(height: 7),
            Text(
              message,
              style: T.body(11.5, color: C.muted, height: 1.45),
            ),
            const SizedBox(height: 10),
            ActionButton(label: 'Повторить', onTap: onRetry, dense: true),
          ],
        ),
      );
}

String _healthLabel(String value) => switch (value) {
      'BROKEN_INPUT' => 'BROKEN INPUT',
      'NO_SAMPLE' => 'NO SAMPLE',
      'DEGRADED' => 'DEGRADED',
      'OK' => 'OK',
      _ => value.replaceAll('_', ' '),
    };

String _verdictLabel(String value) => switch (value) {
      'BROKEN_INPUT' => 'BROKEN INPUT',
      'WAITING_FOR_SAMPLE' => 'ЖДЁМ ВЫБОРКУ',
      'INSUFFICIENT_OUTCOMES' => 'ЖДЁМ ИСХОДЫ',
      'CANDIDATE_WINNING' => 'КАНДИДАТ ЛУЧШЕ',
      'CONTROL_WINNING' => 'CONTROL ЛУЧШЕ',
      _ => value.replaceAll('_', ' '),
    };

Color _statusColor(String value) => switch (value) {
      'BROKEN_INPUT' => C.red,
      'OK' || 'CANDIDATE_WINNING' => C.green,
      'CONTROL_WINNING' => C.info,
      'DEGRADED' || 'WAITING_FOR_SAMPLE' || 'INSUFFICIENT_OUTCOMES' => C.warning,
      _ => C.muted,
    };

Color _statusBorder(String value) => switch (value) {
      'BROKEN_INPUT' => C.redBorder,
      'OK' || 'CANDIDATE_WINNING' => C.greenBorder,
      'CONTROL_WINNING' => C.infoBorder,
      'DEGRADED' || 'WAITING_FOR_SAMPLE' || 'INSUFFICIENT_OUTCOMES' =>
        C.warningBorder,
      _ => C.border,
    };

String _n(double? value, {int digits = 2}) =>
    value == null ? '—' : value.toStringAsFixed(digits).replaceAll('.', ',');

String _r(double? value) => value == null ? '—' : '${_n(value)}R';

String _dotN(double? value, {int digits = 2}) =>
    value == null ? '—' : value.toStringAsFixed(digits);

String _signedR(double? value) {
  if (value == null) return '—';
  final sign = value > 0 ? '+' : '';
  return '$sign${value.toStringAsFixed(2)}R';
}

String _rMagnitude(double? value) =>
    value == null ? '—' : '${value.toStringAsFixed(2)}R';

String _configN(Object? value) =>
    value is num ? _n(value.toDouble()) : '—';

String _configR(Object? value) =>
    value is num ? _r(value.toDouble()) : '—';

String _shortHash(String value) =>
    value.length <= 12 ? value : value.substring(0, 12);

String _shortTime(String value) {
  final parsed = DateTime.tryParse(value)?.toLocal();
  if (parsed == null) return value;
  final day = parsed.day.toString().padLeft(2, '0');
  final month = parsed.month.toString().padLeft(2, '0');
  final hour = parsed.hour.toString().padLeft(2, '0');
  final minute = parsed.minute.toString().padLeft(2, '0');
  return '$day.$month $hour:$minute';
}

String _counts(Map<String, int> values) => values.entries
    .map((entry) => '${entry.key} ${entry.value}')
    .join(' · ');
