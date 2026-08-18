enum ExperimentComparisonStatus { noExperiments, pending, ready }

/// Owner-facing snapshot of persisted champion/challenger evidence.
///
/// This model is deliberately read-only: it describes measurement evidence
/// and never carries a command that could mutate strategy roles or runtime
/// execution.
class ExperimentComparison {
  const ExperimentComparison.noExperiments()
      : status = ExperimentComparisonStatus.noExperiments,
        experimentId = null,
        name = null,
        controlVersion = null,
        candidateVersion = null,
        stage = null,
        sampleSize = null,
        sampleAdequate = null,
        netExpectancyDeltaR = null,
        maxDrawdownDeltaR = null,
        hitRateDelta = null,
        calibrationDelta = null,
        opportunityOverlap = null,
        latestDecision = null;

  const ExperimentComparison.pending({
    required this.experimentId,
    required this.name,
    required this.controlVersion,
    required this.candidateVersion,
    required this.stage,
    this.sampleSize,
    this.sampleAdequate,
  })  : status = ExperimentComparisonStatus.pending,
        netExpectancyDeltaR = null,
        maxDrawdownDeltaR = null,
        hitRateDelta = null,
        calibrationDelta = null,
        opportunityOverlap = null,
        latestDecision = null;

  const ExperimentComparison.ready({
    required this.experimentId,
    required this.name,
    required this.controlVersion,
    required this.candidateVersion,
    required this.stage,
    required this.sampleSize,
    required this.sampleAdequate,
    this.netExpectancyDeltaR,
    this.maxDrawdownDeltaR,
    this.hitRateDelta,
    this.calibrationDelta,
    this.opportunityOverlap,
    this.latestDecision,
  }) : status = ExperimentComparisonStatus.ready;

  final ExperimentComparisonStatus status;
  final String? experimentId;
  final String? name;
  final String? controlVersion;
  final String? candidateVersion;
  final String? stage;
  final int? sampleSize;
  final bool? sampleAdequate;
  final double? netExpectancyDeltaR;
  final double? maxDrawdownDeltaR;
  final double? hitRateDelta;
  final double? calibrationDelta;
  final double? opportunityOverlap;
  final String? latestDecision;
}
