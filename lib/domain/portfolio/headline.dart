import 'package:flutter/foundation.dart';

import 'package.dart';

/// Owner-facing state of one investment profile slot.
enum PortfolioHeadlineStatus {
  ready,
  riskierThanTarget,
  missing;

  static PortfolioHeadlineStatus parse(Object? raw) => switch ('$raw') {
        'ready' => PortfolioHeadlineStatus.ready,
        'riskier_than_target' => PortfolioHeadlineStatus.riskierThanTarget,
        _ => PortfolioHeadlineStatus.missing,
      };
}

/// One of the three investment choices visible to the owner.
///
/// The server chooses the internal package variant. The mobile app must not
/// reconstruct that choice from the optimiser's profile × package matrix.
@immutable
class PortfolioHeadline {
  const PortfolioHeadline({
    required this.profile,
    required this.label,
    required this.status,
    required this.reason,
    this.package,
  });

  final String profile;
  final String label;
  final PortfolioHeadlineStatus status;
  final String reason;
  final EnginePackage? package;

  bool get hasPackage => package != null;
  bool get isRiskier => status == PortfolioHeadlineStatus.riskierThanTarget;
  bool get isMissing => status == PortfolioHeadlineStatus.missing;
}

/// Exact-horizon owner portfolio response.
@immutable
class PortfolioHeadlines {
  const PortfolioHeadlines({
    required this.horizonYears,
    required this.portfolios,
    this.unavailableReason,
  });

  const PortfolioHeadlines.unavailable(
    String reason, {
    required this.horizonYears,
  })  : portfolios = const [],
        unavailableReason = reason;

  final int horizonYears;
  final List<PortfolioHeadline> portfolios;
  final String? unavailableReason;

  bool get isAvailable => unavailableReason == null;
}
