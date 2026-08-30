import 'api_client.dart';

/// Read-only client for the owner strategy/risk control plane.
class ControlDashboardClient {
  ControlDashboardClient([ApiClient? api]) : _api = api ?? ApiClient();

  final ApiClient _api;

  Future<ControlDashboardSnapshot> load({
    required String venue,
    int windowHours = 168,
  }) async {
    if (venue != 'FORTS' && venue != 'BYBIT') {
      throw ArgumentError.value(venue, 'venue', 'must be FORTS or BYBIT');
    }
    if (windowHours < 1) {
      throw ArgumentError.value(windowHours, 'windowHours', 'must be positive');
    }
    final json = await _api.get(
      '/api/v1/control/dashboard?venue=$venue&window_hours=$windowHours',
    );
    final result = ControlDashboardSnapshot.fromJson(json);
    if (result.venue != venue) {
      throw FormatException(
        'Control dashboard venue mismatch: requested $venue, got ${result.venue}',
      );
    }
    return result;
  }
}

class ControlDashboardSnapshot {
  const ControlDashboardSnapshot({
    required this.generatedAt,
    required this.venue,
    required this.windowHours,
    required this.health,
    required this.funnel,
    required this.competition,
    required this.backtest,
    required this.riskOptimizer,
    required this.runtimeRoles,
    required this.dataReadiness,
  });

  final String generatedAt;
  final String venue;
  final int windowHours;
  final String health;
  final ControlFunnelSnapshot funnel;
  final ControlCompetitionSnapshot competition;
  final ControlBacktestSnapshot backtest;
  final RiskOptimizerSnapshot riskOptimizer;

  /// Newer server contract. Nullable keeps older saved fixtures and compatible
  /// servers readable while the live API always emits this field.
  final ControlRuntimeRoles? runtimeRoles;

  /// BYBIT-only immutable historical evidence. FORTS/older payloads may omit it.
  final BybitDataReadiness? dataReadiness;

  factory ControlDashboardSnapshot.fromJson(Map<String, dynamic> json) =>
      ControlDashboardSnapshot(
        generatedAt: _requiredString(json['generated_at'], 'generated_at'),
        venue: _requiredString(json['venue'], 'venue'),
        windowHours: _requiredInt(json['window_hours'], 'window_hours'),
        health: _requiredString(json['health'], 'health'),
        funnel: ControlFunnelSnapshot.fromJson(_requiredMap(json['funnel'], 'funnel')),
        competition: ControlCompetitionSnapshot.fromJson(
          _requiredMap(json['competition'], 'competition'),
        ),
        backtest: ControlBacktestSnapshot.fromJson(
          _requiredMap(json['backtest'], 'backtest'),
        ),
        riskOptimizer: RiskOptimizerSnapshot.fromJson(
          _requiredMap(json['risk_optimizer'], 'risk_optimizer'),
        ),
        runtimeRoles: json['runtime_roles'] == null
            ? null
            : ControlRuntimeRoles.fromJson(
                _requiredMap(json['runtime_roles'], 'runtime_roles'),
              ),
        dataReadiness: json['data_readiness'] == null
            ? null
            : BybitDataReadiness.fromJson(
                _requiredMap(json['data_readiness'], 'data_readiness'),
              ),
      );
}

class ControlRuntimeLiveGenerator {
  const ControlRuntimeLiveGenerator({
    required this.version,
    required this.publishesTradeIdeas,
    required this.strategyFamilies,
  });

  final String version;
  final bool publishesTradeIdeas;
  final List<String> strategyFamilies;

  factory ControlRuntimeLiveGenerator.fromJson(Map<String, dynamic> json) =>
      ControlRuntimeLiveGenerator(
        version: _requiredString(
          json['version'],
          'runtime_roles.live_generator.version',
        ),
        publishesTradeIdeas: _requiredBool(
          json['publishes_trade_ideas'],
          'runtime_roles.live_generator.publishes_trade_ideas',
        ),
        strategyFamilies: _stringList(
          json['strategy_families'],
          'runtime_roles.live_generator.strategy_families',
        ),
      );
}

class ControlRuntimeRoles {
  const ControlRuntimeRoles({
    required this.liveGenerator,
    required this.champion,
    required this.challengers,
    required this.shadowOnly,
    required this.governanceControlsRuntime,
    required this.explanation,
  });

  final ControlRuntimeLiveGenerator liveGenerator;
  final String? champion;
  final List<String> challengers;
  final List<String> shadowOnly;
  final bool governanceControlsRuntime;
  final String explanation;

  factory ControlRuntimeRoles.fromJson(Map<String, dynamic> json) =>
      ControlRuntimeRoles(
        liveGenerator: ControlRuntimeLiveGenerator.fromJson(
          _requiredMap(json['live_generator'], 'runtime_roles.live_generator'),
        ),
        champion: _optionalString(json['champion'], 'runtime_roles.champion'),
        challengers: _stringList(
          json['challengers'],
          'runtime_roles.challengers',
        ),
        shadowOnly: _stringList(
          json['shadow_only'],
          'runtime_roles.shadow_only',
        ),
        governanceControlsRuntime: _requiredBool(
          json['governance_controls_runtime'],
          'runtime_roles.governance_controls_runtime',
        ),
        explanation: _requiredString(
          json['explanation'],
          'runtime_roles.explanation',
        ),
      );
}

class ControlFunnelSnapshot {
  const ControlFunnelSnapshot({
    required this.control,
    required this.candidates,
    required this.scan,
  });

  final LegacyControlFunnel control;
  final List<ShadowControlSummary> candidates;
  final BybitScanFunnel? scan;

  factory ControlFunnelSnapshot.fromJson(Map<String, dynamic> json) =>
      ControlFunnelSnapshot(
        control: LegacyControlFunnel.fromJson(
          _requiredMap(json['control'], 'funnel.control'),
        ),
        candidates: _mapList(json['candidates'], 'funnel.candidates')
            .map(ShadowControlSummary.fromJson)
            .toList(growable: false),
        scan: json['scan'] == null
            ? null
            : BybitScanFunnel.fromJson(
                _requiredMap(json['scan'], 'funnel.scan'),
              ),
      );
}

class FunnelReasonSummary {
  const FunnelReasonSummary({required this.reason, required this.count});

  final String reason;
  final int count;

  factory FunnelReasonSummary.fromJson(Map<String, dynamic> json) =>
      FunnelReasonSummary(
        reason: _requiredString(json['reason'], 'funnel.scan.reason'),
        count: _requiredInt(json['count'], 'funnel.scan.count'),
      );
}

class BybitScanFunnel {
  const BybitScanFunnel({
    required this.universe,
    required this.dataHealthy,
    required this.liquid,
    required this.regimeEligible,
    required this.strategyEvaluated,
    required this.setupReject,
    required this.costRrReject,
    required this.published,
    required this.terminal,
    required this.topReasons,
  });

  final int universe;
  final int dataHealthy;
  final int liquid;
  final int regimeEligible;
  final int strategyEvaluated;
  final int setupReject;
  final int costRrReject;
  final int published;
  final Map<String, int> terminal;
  final List<FunnelReasonSummary> topReasons;

  factory BybitScanFunnel.fromJson(Map<String, dynamic> json) => BybitScanFunnel(
        universe: _requiredInt(json['universe'], 'funnel.scan.universe'),
        dataHealthy: _requiredInt(
          json['data_healthy'],
          'funnel.scan.data_healthy',
        ),
        liquid: _requiredInt(json['liquid'], 'funnel.scan.liquid'),
        regimeEligible: _requiredInt(
          json['regime_eligible'],
          'funnel.scan.regime_eligible',
        ),
        strategyEvaluated: _requiredInt(
          json['strategy_evaluated'],
          'funnel.scan.strategy_evaluated',
        ),
        setupReject: _requiredInt(
          json['setup_reject'],
          'funnel.scan.setup_reject',
        ),
        costRrReject: _requiredInt(
          json['cost_rr_reject'],
          'funnel.scan.cost_rr_reject',
        ),
        published: _requiredInt(json['published'], 'funnel.scan.published'),
        terminal: _intMap(json['terminal'], 'funnel.scan.terminal'),
        topReasons: _mapList(json['top_reasons'], 'funnel.scan.top_reasons')
            .map(FunnelReasonSummary.fromJson)
            .toList(growable: false),
      );
}

class BybitStreamCoverage {
  const BybitStreamCoverage({
    required this.stream,
    required this.ready,
    required this.reason,
  });

  final String stream;
  final bool ready;
  final String reason;

  factory BybitStreamCoverage.fromJson(Map<String, dynamic> json) =>
      BybitStreamCoverage(
        stream: _requiredString(json['stream'], 'data_readiness.coverage.stream'),
        ready: _requiredBool(json['ready'], 'data_readiness.coverage.ready'),
        reason: _requiredString(json['reason'], 'data_readiness.coverage.reason'),
      );
}

class BybitDatasetReadinessSymbol {
  const BybitDatasetReadinessSymbol({
    required this.symbol,
    required this.status,
    required this.snapshotId,
    required this.contentSha256,
    required this.tradableAt,
    required this.rowCount,
    required this.coverage,
  });

  final String symbol;
  final String status;
  final String snapshotId;
  final String contentSha256;
  final String tradableAt;
  final int rowCount;
  final List<BybitStreamCoverage> coverage;

  factory BybitDatasetReadinessSymbol.fromJson(Map<String, dynamic> json) =>
      BybitDatasetReadinessSymbol(
        symbol: _requiredString(json['symbol'], 'data_readiness.symbol'),
        status: _requiredString(json['status'], 'data_readiness.status'),
        snapshotId: _requiredString(
          json['snapshot_id'],
          'data_readiness.snapshot_id',
        ),
        contentSha256: _requiredString(
          json['content_sha256'],
          'data_readiness.content_sha256',
        ),
        tradableAt: _requiredString(
          json['tradable_at'],
          'data_readiness.tradable_at',
        ),
        rowCount: _requiredInt(json['row_count'], 'data_readiness.row_count'),
        coverage: _mapList(json['coverage'], 'data_readiness.coverage')
            .map(BybitStreamCoverage.fromJson)
            .toList(growable: false),
      );
}

class BybitDataReadiness {
  const BybitDataReadiness({required this.status, required this.symbols});

  final String status;
  final List<BybitDatasetReadinessSymbol> symbols;

  factory BybitDataReadiness.fromJson(Map<String, dynamic> json) =>
      BybitDataReadiness(
        status: _requiredString(json['status'], 'data_readiness.status'),
        symbols: _mapList(json['symbols'], 'data_readiness.symbols')
            .map(BybitDatasetReadinessSymbol.fromJson)
            .toList(growable: false),
      );
}

class LegacyControlFunnel {
  const LegacyControlFunnel({
    required this.ideasCreated,
    required this.presented,
    required this.statuses,
    required this.qualities,
    required this.versions,
  });

  final int ideasCreated;
  final int presented;
  final Map<String, int> statuses;
  final Map<String, int> qualities;
  final Map<String, int> versions;

  factory LegacyControlFunnel.fromJson(Map<String, dynamic> json) =>
      LegacyControlFunnel(
        ideasCreated: _requiredInt(json['ideas_created'], 'ideas_created'),
        presented: _requiredInt(json['presented'], 'presented'),
        statuses: _intMap(json['statuses'], 'statuses'),
        qualities: _intMap(json['qualities'], 'qualities'),
        versions: _intMap(json['versions'], 'versions'),
      );
}

class ShadowUnavailableReason {
  const ShadowUnavailableReason({required this.reason, required this.count});

  final String reason;
  final int count;

  factory ShadowUnavailableReason.fromJson(Map<String, dynamic> json) =>
      ShadowUnavailableReason(
        reason: _requiredString(json['reason'], 'reason'),
        count: _requiredInt(json['count'], 'count'),
      );
}

class ShadowControlSummary {
  const ShadowControlSummary({
    required this.version,
    required this.observations,
    required this.evaluated,
    required this.unavailable,
    required this.emitted,
    required this.topUnavailableReasons,
  });

  final String version;
  final int observations;
  final int evaluated;
  final int unavailable;
  final int emitted;
  final List<ShadowUnavailableReason> topUnavailableReasons;

  factory ShadowControlSummary.fromJson(Map<String, dynamic> json) =>
      ShadowControlSummary(
        version: _requiredString(json['version'], 'shadow.version'),
        observations: _requiredInt(json['observations'], 'shadow.observations'),
        evaluated: _requiredInt(json['evaluated'], 'shadow.evaluated'),
        unavailable: _requiredInt(json['unavailable'], 'shadow.unavailable'),
        emitted: _requiredInt(json['emitted'], 'shadow.emitted'),
        topUnavailableReasons: _mapList(
          json['top_unavailable_reasons'],
          'shadow.top_unavailable_reasons',
        ).map(ShadowUnavailableReason.fromJson).toList(growable: false),
      );
}

class PaperArmSummary {
  const PaperArmSummary({
    required this.decisions,
    required this.emitted,
    required this.evaluatedOutcomes,
    required this.pendingOutcomes,
    required this.unavailableOutcomes,
    required this.meanNetR,
  });

  final int decisions;
  final int emitted;
  final int evaluatedOutcomes;
  final int pendingOutcomes;
  final int unavailableOutcomes;
  final double? meanNetR;

  factory PaperArmSummary.fromJson(Map<String, dynamic> json) => PaperArmSummary(
        decisions: _requiredInt(json['decisions'], 'paper.decisions'),
        emitted: _requiredInt(json['emitted'], 'paper.emitted'),
        evaluatedOutcomes: _requiredInt(
          json['evaluated_outcomes'],
          'paper.evaluated_outcomes',
        ),
        pendingOutcomes: _requiredInt(
          json['pending_outcomes'],
          'paper.pending_outcomes',
        ),
        unavailableOutcomes: _requiredInt(
          json['unavailable_outcomes'],
          'paper.unavailable_outcomes',
        ),
        meanNetR: _optionalDouble(json['mean_net_r'], 'paper.mean_net_r'),
      );
}

class PaperComparisonSummary {
  const PaperComparisonSummary({
    required this.control,
    required this.candidate,
    required this.comparablePairs,
    required this.controlMeanNetR,
    required this.candidateMeanNetR,
    required this.deltaMeanNetR,
  });

  final PaperArmSummary control;
  final PaperArmSummary candidate;
  final int comparablePairs;
  final double? controlMeanNetR;
  final double? candidateMeanNetR;
  final double? deltaMeanNetR;

  factory PaperComparisonSummary.fromJson(Map<String, dynamic> json) =>
      PaperComparisonSummary(
        control: PaperArmSummary.fromJson(
          _requiredMap(json['control'], 'paper.control'),
        ),
        candidate: PaperArmSummary.fromJson(
          _requiredMap(json['candidate'], 'paper.candidate'),
        ),
        comparablePairs: _requiredInt(
          json['comparable_pairs'],
          'paper.comparable_pairs',
        ),
        controlMeanNetR: _optionalDouble(
          json['control_mean_net_r'],
          'paper.control_mean_net_r',
        ),
        candidateMeanNetR: _optionalDouble(
          json['candidate_mean_net_r'],
          'paper.candidate_mean_net_r',
        ),
        deltaMeanNetR: _optionalDouble(
          json['delta_mean_net_r'],
          'paper.delta_mean_net_r',
        ),
      );
}

class CompetitionCandidateSummary {
  const CompetitionCandidateSummary({
    required this.version,
    required this.verdict,
    required this.shadow,
    required this.paper,
  });

  final String version;
  final String verdict;
  final ShadowControlSummary shadow;
  final PaperComparisonSummary paper;

  factory CompetitionCandidateSummary.fromJson(Map<String, dynamic> json) =>
      CompetitionCandidateSummary(
        version: _requiredString(json['version'], 'candidate.version'),
        verdict: _requiredString(json['verdict'], 'candidate.verdict'),
        shadow: ShadowControlSummary.fromJson(
          _requiredMap(json['shadow'], 'candidate.shadow'),
        ),
        paper: PaperComparisonSummary.fromJson(
          _requiredMap(json['paper'], 'candidate.paper'),
        ),
      );
}

class ControlCompetitionSnapshot {
  const ControlCompetitionSnapshot({
    required this.controlVersion,
    required this.minComparableSample,
    required this.candidates,
  });

  final String controlVersion;
  final int minComparableSample;
  final List<CompetitionCandidateSummary> candidates;

  factory ControlCompetitionSnapshot.fromJson(Map<String, dynamic> json) =>
      ControlCompetitionSnapshot(
        controlVersion: _requiredString(
          json['control_version'],
          'competition.control_version',
        ),
        minComparableSample: _requiredInt(
          json['min_comparable_sample'],
          'competition.min_comparable_sample',
        ),
        candidates: _mapList(json['candidates'], 'competition.candidates')
            .map(CompetitionCandidateSummary.fromJson)
            .toList(growable: false),
      );
}

class BacktestRunSummary {
  const BacktestRunSummary({
    required this.label,
    required this.strategy,
    required this.periodFrom,
    required this.periodTo,
    required this.trades,
    required this.netReturn,
    required this.profitFactor,
    required this.expectancyR,
    required this.maxDrawdown,
    required this.sharpe,
    required this.sortino,
    required this.calmar,
    required this.brierScore,
    required this.pbo,
    required this.top5Contribution,
    required this.gatePassed,
    required this.configHash,
    required this.engineVersion,
    required this.universe,
    required this.report,
    required this.gateDetail,
  });

  final String label;
  final String? strategy;
  final String periodFrom;
  final String periodTo;
  final int trades;
  final double? netReturn;
  final double? profitFactor;
  final double? expectancyR;
  final double? maxDrawdown;
  final double? sharpe;
  final double? sortino;
  final double? calmar;
  final double? brierScore;
  final double? pbo;
  final double? top5Contribution;
  final bool gatePassed;
  final String configHash;
  final String engineVersion;
  final List<String> universe;
  final Map<String, dynamic> report;
  final Map<String, dynamic> gateDetail;

  factory BacktestRunSummary.fromJson(Map<String, dynamic> json) =>
      BacktestRunSummary(
        label: _requiredString(json['label'], 'backtest.label'),
        strategy: _optionalString(json['strategy'], 'backtest.strategy'),
        periodFrom: _requiredString(json['period_from'], 'backtest.period_from'),
        periodTo: _requiredString(json['period_to'], 'backtest.period_to'),
        trades: _requiredInt(json['trades'], 'backtest.trades'),
        netReturn: _optionalDouble(json['net_return'], 'backtest.net_return'),
        profitFactor: _optionalDouble(
          json['profit_factor'],
          'backtest.profit_factor',
        ),
        expectancyR: _optionalDouble(
          json['expectancy_r'],
          'backtest.expectancy_r',
        ),
        maxDrawdown: _optionalDouble(
          json['max_drawdown'],
          'backtest.max_drawdown',
        ),
        sharpe: _optionalDouble(json['sharpe'], 'backtest.sharpe'),
        sortino: _optionalDouble(json['sortino'], 'backtest.sortino'),
        calmar: _optionalDouble(json['calmar'], 'backtest.calmar'),
        brierScore: _optionalDouble(
          json['brier_score'],
          'backtest.brier_score',
        ),
        pbo: _optionalDouble(json['pbo'], 'backtest.pbo'),
        top5Contribution: _optionalDouble(
          json['top5_contribution'],
          'backtest.top5_contribution',
        ),
        gatePassed: _requiredBool(json['gate_passed'], 'backtest.gate_passed'),
        configHash: _requiredString(json['config_hash'], 'backtest.config_hash'),
        engineVersion: _requiredString(
          json['engine_version'],
          'backtest.engine_version',
        ),
        universe: _stringList(json['universe'], 'backtest.universe'),
        report: _requiredMap(json['report'], 'backtest.report'),
        gateDetail: _requiredMap(json['gate_detail'], 'backtest.gate_detail'),
      );
}

class ControlBacktestSnapshot {
  const ControlBacktestSnapshot({
    required this.latest,
    required this.byStrategy,
    required this.walkForward,
    required this.paperGate,
    required this.liveGate,
  });

  final BacktestRunSummary? latest;
  final List<BacktestRunSummary> byStrategy;
  final Map<String, dynamic> walkForward;
  final Map<String, dynamic> paperGate;
  final Map<String, dynamic> liveGate;

  factory ControlBacktestSnapshot.fromJson(Map<String, dynamic> json) =>
      ControlBacktestSnapshot(
        latest: json['latest'] == null
            ? null
            : BacktestRunSummary.fromJson(
                _requiredMap(json['latest'], 'backtest.latest'),
              ),
        byStrategy: json['by_strategy'] == null
            ? const <BacktestRunSummary>[]
            : _mapList(json['by_strategy'], 'backtest.by_strategy')
                .map(BacktestRunSummary.fromJson)
                .toList(growable: false),
        walkForward: _requiredMap(json['walk_forward'], 'backtest.walk_forward'),
        paperGate: _requiredMap(json['paper_gate'], 'backtest.paper_gate'),
        liveGate: _requiredMap(json['live_gate'], 'backtest.live_gate'),
      );
}

class RiskChampionSummary {
  const RiskChampionSummary({
    required this.version,
    required this.candidateId,
    required this.algorithm,
    required this.sampleSize,
    required this.trainedFrom,
    required this.trainedTo,
    required this.promotedAt,
    required this.metrics,
    required this.llmReview,
    required this.absoluteRiskCapsChanged,
  });

  final String version;
  final String? candidateId;
  final String algorithm;
  final int sampleSize;
  final String trainedFrom;
  final String trainedTo;
  final String? promotedAt;
  final Map<String, dynamic> metrics;
  final Map<String, dynamic> llmReview;
  final bool absoluteRiskCapsChanged;

  factory RiskChampionSummary.fromJson(Map<String, dynamic> json) =>
      RiskChampionSummary(
        version: _requiredString(json['version'], 'risk.champion.version'),
        candidateId: _optionalString(
          json['candidate_id'],
          'risk.champion.candidate_id',
        ),
        algorithm: _requiredString(
          json['algorithm'],
          'risk.champion.algorithm',
        ),
        sampleSize: _requiredInt(
          json['sample_size'],
          'risk.champion.sample_size',
        ),
        trainedFrom: _requiredString(
          json['trained_from'],
          'risk.champion.trained_from',
        ),
        trainedTo: _requiredString(
          json['trained_to'],
          'risk.champion.trained_to',
        ),
        promotedAt: _optionalString(
          json['promoted_at'],
          'risk.champion.promoted_at',
        ),
        metrics: _requiredMap(json['metrics'], 'risk.champion.metrics'),
        llmReview: _requiredMap(json['llm_review'], 'risk.champion.llm_review'),
        absoluteRiskCapsChanged: _requiredBool(
          json['absolute_risk_caps_changed'],
          'risk.champion.absolute_risk_caps_changed',
        ),
      );
}

class RiskOptimizerConfigSummary {
  const RiskOptimizerConfigSummary({
    required this.cadenceDays,
    required this.minSamples,
    required this.minOosExpectancyImprovementR,
    required this.candidateIds,
    required this.absoluteRiskCapsMutable,
  });

  final int cadenceDays;
  final int minSamples;
  final double minOosExpectancyImprovementR;
  final List<String> candidateIds;
  final bool absoluteRiskCapsMutable;

  factory RiskOptimizerConfigSummary.fromJson(Map<String, dynamic> json) =>
      RiskOptimizerConfigSummary(
        cadenceDays: _requiredInt(json['cadence_days'], 'risk.config.cadence_days'),
        minSamples: _requiredInt(json['min_samples'], 'risk.config.min_samples'),
        minOosExpectancyImprovementR: _requiredDouble(
          json['min_oos_expectancy_improvement_r'],
          'risk.config.min_oos_expectancy_improvement_r',
        ),
        candidateIds: _stringList(
          json['candidate_ids'],
          'risk.config.candidate_ids',
        ),
        absoluteRiskCapsMutable: _requiredBool(
          json['absolute_risk_caps_mutable'],
          'risk.config.absolute_risk_caps_mutable',
        ),
      );
}

class RiskOptimizerSnapshot {
  const RiskOptimizerSnapshot({
    required this.champion,
    required this.latestRun,
    required this.nextDueAt,
    required this.config,
  });

  final RiskChampionSummary? champion;
  final BacktestRunSummary? latestRun;
  final String? nextDueAt;
  final RiskOptimizerConfigSummary config;

  factory RiskOptimizerSnapshot.fromJson(Map<String, dynamic> json) =>
      RiskOptimizerSnapshot(
        champion: json['champion'] == null
            ? null
            : RiskChampionSummary.fromJson(
                _requiredMap(json['champion'], 'risk.champion'),
              ),
        latestRun: json['latest_run'] == null
            ? null
            : BacktestRunSummary.fromJson(
                _requiredMap(json['latest_run'], 'risk.latest_run'),
              ),
        nextDueAt: _optionalString(json['next_due_at'], 'risk.next_due_at'),
        config: RiskOptimizerConfigSummary.fromJson(
          _requiredMap(json['config'], 'risk.config'),
        ),
      );
}

Map<String, dynamic> _requiredMap(Object? value, String field) {
  if (value is Map<String, dynamic>) return value;
  if (value is Map) {
    return value.map((key, item) => MapEntry(key.toString(), item));
  }
  throw FormatException('$field must be an object');
}

List<Map<String, dynamic>> _mapList(Object? value, String field) {
  if (value is! List) throw FormatException('$field must be a list');
  return value
      .map((item) => _requiredMap(item, '$field[]'))
      .toList(growable: false);
}

Map<String, int> _intMap(Object? value, String field) {
  final map = _requiredMap(value, field);
  return map.map(
    (key, item) => MapEntry(key, _requiredInt(item, '$field.$key')),
  );
}

List<String> _stringList(Object? value, String field) {
  if (value is! List) throw FormatException('$field must be a list');
  return value
      .map((item) => _requiredString(item, '$field[]'))
      .toList(growable: false);
}

String _requiredString(Object? value, String field) {
  if (value is String && value.isNotEmpty) return value;
  throw FormatException('$field must be a non-empty string');
}

String? _optionalString(Object? value, String field) {
  if (value == null) return null;
  if (value is String) return value;
  throw FormatException('$field must be a string or null');
}

int _requiredInt(Object? value, String field) {
  if (value is int) return value;
  if (value is num && value.isFinite && value == value.roundToDouble()) {
    return value.toInt();
  }
  throw FormatException('$field must be an integer');
}

double _requiredDouble(Object? value, String field) {
  if (value is num && value.isFinite) return value.toDouble();
  throw FormatException('$field must be a finite number');
}

double? _optionalDouble(Object? value, String field) {
  if (value == null) return null;
  return _requiredDouble(value, field);
}

bool _requiredBool(Object? value, String field) {
  if (value is bool) return value;
  throw FormatException('$field must be a boolean');
}
