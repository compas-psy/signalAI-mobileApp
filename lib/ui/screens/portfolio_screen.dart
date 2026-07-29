import 'package:flutter/widgets.dart';

import '../../state/navigation.dart';
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
    final section =
        PortfolioPill.values[pill.clamp(0, PortfolioPill.values.length - 1)];
    return CapitalScreen(
      pill: switch (section) {
        PortfolioPill.packages => CapitalPill.packages.index,
        PortfolioPill.rebalance => CapitalPill.overview.index,
        PortfolioPill.accounts => CapitalPill.accounts.index,
      },
    );
  }
}
