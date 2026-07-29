import 'package:flutter/widgets.dart';

import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'vector_icon.dart';

/// Нижняя навигация: Сегодня · Портфель · Идеи · Журнал · Настройки.
class BottomNav extends StatelessWidget {
  const BottomNav({super.key, required this.current, required this.onSelect});

  final AppSection current;
  final ValueChanged<AppSection> onSelect;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(8, 6, 8, 4),
        decoration: const BoxDecoration(
          color: C.navBg,
          border: Border(top: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            for (final item in navItems)
              _item(item.section, item.label, item.icon(C.navBg)),
          ],
        ),
      );

  Widget _item(AppSection section, String label, IconSpec icon) {
    final active = section == current;
    final color = active ? C.accent : C.navInactive;
    return Expanded(
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onSelect(section),
        child: Container(
          constraints: const BoxConstraints(minHeight: 44),
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              VectorIcon(icon, size: 20, color: color),
              const SizedBox(height: 3),
              Text(
                label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: T.body(9.5, weight: 700, color: color),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Пункт навигации: раздел, подпись и знак.
class NavItem {
  const NavItem(this.section, this.label, this.icon);

  final AppSection section;
  final String label;

  /// Иконка получает цвет фона для «прорезей» (у слайдеров стратегий).
  final IconSpec Function(Color background) icon;
}

/// Порядок разделов — один для нижней панели и бокового рейла.
const navItems = <NavItem>[
  NavItem(AppSection.today, 'Сегодня', _today),
  NavItem(AppSection.portfolio, 'Портфель', _portfolio),
  NavItem(AppSection.ideas, 'Идеи', _ideas),
  NavItem(AppSection.journal, 'Журнал', _journal),
  NavItem(AppSection.settings, 'Настройки', _settings),
];

IconSpec _today(Color _) => Icons.navToday;
IconSpec _portfolio(Color _) => Icons.navInvest;
IconSpec _ideas(Color _) => Icons.navIdeas;
IconSpec _journal(Color background) => Icons.navStrategies(background);
IconSpec _settings(Color _) => Icons.navSettings;
