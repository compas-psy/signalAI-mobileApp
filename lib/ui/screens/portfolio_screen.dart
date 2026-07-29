import 'package:flutter/widgets.dart';

import '../../domain/portfolio/package_plan.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../widgets/segmented.dart';
import 'capital_screen.dart';

/// Раздел «Портфель» (ТЗ §7).
///
/// Пакеты, черновик ребалансировки и счета. Содержимое пока берётся из
/// прежнего раздела «Капитал»: он читает те же книгу, счета и цены, и
/// выбрасывать рабочий код ради переименования было бы вредительством.
/// Экраны переводятся на модель ТЗ §7.1 по одному, а не разом.
class PortfolioScreen extends StatelessWidget {
  const PortfolioScreen({super.key, required this.pill});

  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final section =
        PortfolioPill.values[pill.clamp(0, PortfolioPill.values.length - 1)];
    final body = CapitalScreen(
      pill: switch (section) {
        PortfolioPill.packages => CapitalPill.packages.index,
        PortfolioPill.rebalance => CapitalPill.overview.index,
        PortfolioPill.accounts => CapitalPill.accounts.index,
      },
    );

    // Горизонт переключает не оформление, а состав: на годовом сроке
    // просадку рынка акций пересидеть нельзя, и веса другие во всех трёх
    // профилях. Поэтому переключатель стоит над пакетами, а не внутри.
    if (section != PortfolioPill.packages) return body;
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
