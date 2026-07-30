import 'package:flutter/widgets.dart';

import '../../domain/portfolio/package_plan.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../widgets/segmented.dart';
import 'capital_screen.dart';
import 'rebalance_screen.dart';

/// Раздел «Портфель» (ТЗ §7).
///
/// Пакеты, фактическая ребалансировка и счета. Старый экран капитала остаётся
/// источником учёта и счетов, но подпункт «Ребалансировка» больше не маскирует
/// обзор P&L под список торговых действий.
class PortfolioScreen extends StatelessWidget {
  const PortfolioScreen({super.key, required this.pill});

  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final section =
        PortfolioPill.values[pill.clamp(0, PortfolioPill.values.length - 1)];
    final Widget body = switch (section) {
      PortfolioPill.packages =>
        CapitalScreen(pill: CapitalPill.packages.index),
      PortfolioPill.rebalance => const RebalanceScreen(),
      PortfolioPill.accounts =>
        CapitalScreen(pill: CapitalPill.accounts.index),
    };

    // Горизонт переключает не оформление, а состав пакетов и предложения по
    // ребалансировке. Поэтому выбор виден и на пакетах, и на действиях.
    if (section == PortfolioPill.accounts) return body;
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
        Expanded(child: body),
      ],
    );
  }
}
