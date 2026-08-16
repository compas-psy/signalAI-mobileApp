import 'package:flutter/widgets.dart';

import '../../domain/portfolio/package_plan.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../widgets/segmented.dart';
import 'capital_screen.dart';
import 'investment_signals_screen.dart';
import 'portfolio_headlines_screen.dart';

/// Раздел «Портфель» (ТЗ §7).
///
/// Пакеты — три owner-facing стратегии от сервера. Инвестиционные сигналы,
/// ребалансировка и счета остаются отдельными рабочими поверхностями.
class PortfolioScreen extends StatelessWidget {
  const PortfolioScreen({super.key, required this.pill});

  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final section =
        PortfolioPill.values[pill.clamp(0, PortfolioPill.values.length - 1)];
    if (section == PortfolioPill.signals) {
      return const InvestmentSignalsScreen();
    }

    if (section == PortfolioPill.packages) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 2),
            child: SegmentedControl(
              items: [for (final h in PackageHorizon.values) h.label],
              index: PackageHorizon.values.indexOf(controller.packageHorizon),
              onSelect: (i) =>
                  controller.setPackageHorizon(PackageHorizon.values[i]),
            ),
          ),
          Expanded(
            child: PortfolioHeadlinesScreen(
              horizonYears: controller.packageHorizon.years,
            ),
          ),
        ],
      );
    }

    return CapitalScreen(
      pill: switch (section) {
        PortfolioPill.packages => CapitalPill.packages.index,
        PortfolioPill.signals => CapitalPill.packages.index,
        // Старый /packages здесь остаётся технической поверхностью: именно
        // он загружает advisory-only сверку счёта с выбранной моделью.
        // В основном chooser внутренние варианты больше не показываются.
        PortfolioPill.rebalance => CapitalPill.packages.index,
        PortfolioPill.accounts => CapitalPill.accounts.index,
      },
    );
  }
}
