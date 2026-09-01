import '../enums.dart';
import 'signal.dart';

/// Structural validation for legacy/on-device [TradingSignal] trade plans.
///
/// Prices are never reordered or repaired here. A malformed plan is evidence
/// that the cached/generated signal is unsafe to act on and must be rejected.
extension TradingSignalGeometry on TradingSignal {
  List<String> tradePlanBlockers({double? minRiskRewardToTp2}) {
    final blockers = <String>[];
    bool positiveFinite(double value) => value.isFinite && value > 0;

    if (!positiveFinite(entry)) blockers.add('entry_invalid');
    if (!positiveFinite(stopLoss)) blockers.add('stop_invalid');

    final risk = priceRisk;
    if (!risk.isFinite || risk <= 0) blockers.add('risk_invalid');

    if (positiveFinite(entry) && positiveFinite(stopLoss)) {
      if (direction == Direction.long && stopLoss >= entry) {
        blockers.add('long_stop_not_below_entry');
      }
      if (direction == Direction.short && stopLoss <= entry) {
        blockers.add('short_stop_not_above_entry');
      }
    }

    if (takeProfits.isEmpty) {
      blockers.add('targets_missing');
    } else {
      double? previous;
      for (final tp in takeProfits) {
        final price = tp.price;
        if (!positiveFinite(price)) {
          blockers.add('target_${tp.index}_invalid');
          previous = price;
          continue;
        }
        if (direction == Direction.long) {
          if (price <= entry) blockers.add('long_target_${tp.index}_not_above_entry');
          if (previous != null && price <= previous) {
            blockers.add('long_targets_not_increasing');
          }
        } else {
          if (price >= entry) blockers.add('short_target_${tp.index}_not_below_entry');
          if (previous != null && price >= previous) {
            blockers.add('short_targets_not_decreasing');
          }
        }
        previous = price;
      }
    }

    if (minRiskRewardToTp2 != null) {
      if (takeProfits.length < 2 || !risk.isFinite || risk <= 0) {
        blockers.add('tp2_risk_reward_unavailable');
      } else {
        final reward = (takeProfits[1].price - entry).abs();
        final rr = reward / risk;
        if (!rr.isFinite || rr + 1e-9 < minRiskRewardToTp2) {
          blockers.add('tp2_risk_reward_below_minimum');
        }
      }
    }

    return blockers;
  }
}
