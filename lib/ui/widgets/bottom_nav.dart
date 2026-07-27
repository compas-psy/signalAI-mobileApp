import 'package:flutter/widgets.dart';

import '../../state/app_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'vector_icon.dart';

/// Нижняя навигация: Идеи · Инвест · Сделки · Стратегии · Настройки.
class BottomNav extends StatelessWidget {
  const BottomNav({
    super.key,
    required this.current,
    required this.detailOpen,
    required this.onSelect,
  });

  final AppTab current;

  /// Открытая карточка идеи подсвечивает вкладку «Идеи».
  final bool detailOpen;
  final ValueChanged<AppTab> onSelect;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(8, 6, 8, 4),
        decoration: const BoxDecoration(
          color: C.navBg,
          border: Border(top: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            _item(AppTab.ideas, 'Идеи', Icons.navIdeas),
            _item(AppTab.invest, 'Инвест', Icons.navInvest),
            _item(AppTab.trades, 'Сделки', Icons.navTrades),
            _item(AppTab.strategies, 'Стратегии', Icons.navStrategies(C.navBg)),
            _item(AppTab.settings, 'Настройки', Icons.navSettings),
          ],
        ),
      );

  Widget _item(AppTab tab, String label, IconSpec icon) {
    final active = detailOpen ? tab == AppTab.ideas : tab == current;
    final color = active ? C.accent : C.navInactive;
    return Expanded(
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onSelect(tab),
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              VectorIcon(icon, size: 20, color: color),
              const SizedBox(height: 3),
              Text(label, style: T.body(10, weight: 700, color: color)),
            ],
          ),
        ),
      ),
    );
  }
}
