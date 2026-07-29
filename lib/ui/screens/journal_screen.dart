import 'package:flutter/widgets.dart';

import '../../domain/models/portfolio.dart';
import '../../state/navigation.dart';
import 'trades_screen.dart';

/// Раздел «Журнал» (ТЗ §12).
///
/// Сделки, пропуски и метрики. Содержимое пока берётся из прежнего экрана
/// позиций: он уже читает журнал бумажных сделок, кривую капитала и отбраковку
/// — то есть ровно те записи, которых требует §12.
class JournalScreen extends StatelessWidget {
  const JournalScreen({super.key, required this.pill, required this.summary});

  final int pill;
  final TradesSummary summary;

  @override
  Widget build(BuildContext context) {
    // Разрез журнала пока один: экран показывает и сделки, и отбраковку, и
    // статистику. Разделение по пилюлям появится вместе с §12.1.
    final _ = JournalPill.values[pill.clamp(0, JournalPill.values.length - 1)];
    return TradesScreen(summary: summary);
  }
}
