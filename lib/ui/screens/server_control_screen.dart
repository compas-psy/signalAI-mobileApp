import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';

/// Owner-facing read-only control plane for strategy evidence.
///
/// This screen never changes strategy roles, thresholds, risk or execution.
/// Server evidence is wired in the next layer; the shell remains useful when
/// the engine is unavailable because its purpose and selected venue are clear.
class ServerControlScreen extends StatefulWidget {
  const ServerControlScreen({super.key});

  @override
  State<ServerControlScreen> createState() => _ServerControlScreenState();
}

class _ServerControlScreenState extends State<ServerControlScreen> {
  int _venue = 1; // BYBIT first: the owner is currently diagnosing crypto flow.

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 28),
        children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Контроль стратегий', style: T.jost(20)),
                const SizedBox(height: 6),
                Text(
                  'read-only · показывает факты конкуренции, бэктеста и risk optimizer; ничего не промоутит и не меняет риск.',
                  style: T.body(11.5, color: C.muted, height: 1.5),
                ),
                const SizedBox(height: 14),
                SegmentedControl(
                  items: const ['FORTS', 'BYBIT'],
                  index: _venue,
                  onSelect: (index) => setState(() => _venue = index),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          const SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionLabel('Данные'),
                SizedBox(height: 8),
                Text(
                  'Загружаем серверный snapshot контроля…',
                  style: TextStyle(color: C.muted, fontSize: 12, height: 1.5),
                ),
              ],
            ),
          ),
        ],
      );
}
