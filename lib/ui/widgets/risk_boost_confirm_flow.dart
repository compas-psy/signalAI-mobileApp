import 'package:flutter/widgets.dart';

import '../../data/api/api_client.dart';
import '../../data/api/engine_client.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/settings.dart';
import '../../domain/models/signal.dart';
import '../../domain/risk/portfolio_impact.dart';
import '../../state/risk_boost_controller.dart';
import 'confirm_sheet.dart';
import 'risk_boost_sheet.dart';

/// Connects the existing trade confirmation sheet to the dedicated manual-risk
/// flow without merging their responsibilities.
///
/// The trade confirmation remains the only path that can call [onExecute]. The
/// «Рискнуть» action opens a separate route with its own [RiskBoostController]
/// and therefore can only preview/apply a server-owned override. Closing or
/// system-back from that route returns to the unchanged trade confirmation.
class RiskBoostConfirmFlow extends StatelessWidget {
  const RiskBoostConfirmFlow({
    super.key,
    required this.ideaId,
    required this.currentMode,
    required this.signal,
    required this.risk,
    required this.onExecute,
    required this.onClose,
    required this.busy,
    required this.paperOnly,
    this.plan,
    this.impact,
    this.engine,
  });

  final String ideaId;
  final String currentMode;
  final TradingSignal signal;
  final TradePlan? plan;
  final RiskProfile risk;
  final PortfolioImpact? impact;
  final VoidCallback onExecute;
  final VoidCallback onClose;
  final bool busy;
  final bool paperOnly;

  /// Injectable for contract/widget tests. Production creates a short-lived
  /// client that reads the already-restored shared engine runtime on every
  /// request and closes its owned HTTP transport when the route exits.
  final EngineClient? engine;

  bool get _canOpenRiskBoost =>
      paperOnly && currentMode == 'PAPER' && ideaId.trim().isNotEmpty;

  @override
  Widget build(BuildContext context) => ConfirmSheet(
        signal: signal,
        plan: plan,
        risk: risk,
        impact: impact,
        busy: busy,
        paperOnly: paperOnly,
        onExecute: onExecute,
        onClose: onClose,
        onRiskBoost: _canOpenRiskBoost ? () => _openRiskBoost(context) : null,
      );

  void _openRiskBoost(BuildContext context) {
    final ownedApi = engine == null ? ApiClient() : null;
    final riskEngine = engine ?? EngineClient(client: ownedApi);
    final controller = RiskBoostController(engine: riskEngine);
    final navigator = Navigator.of(context);
    navigator
        .push<void>(
          PageRouteBuilder<void>(
            opaque: false,
            barrierDismissible: false,
            barrierColor: const Color(0x00000000),
            pageBuilder: (routeContext, _, _) => RiskBoostSheet(
              controller: controller,
              ideaId: ideaId,
              symbol: signal.symbol,
              currentMode: currentMode,
              onClose: () => Navigator.of(routeContext).pop(),
            ),
          ),
        )
        .whenComplete(() {
          controller.dispose();
          ownedApi?.close();
        });
  }
}