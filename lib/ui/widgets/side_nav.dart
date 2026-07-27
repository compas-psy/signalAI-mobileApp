import 'package:flutter/widgets.dart';

import '../../state/app_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'vector_icon.dart';

/// Боковая навигация для планшета: те же разделы, что в нижней панели.
///
/// На широком экране нижняя панель растягивается на всю ширину, и тянуться к
/// её краям неудобно; вертикальная колонка слева держит все разделы в зоне
/// одного движения и оставляет содержимому всю высоту.
class SideNav extends StatelessWidget {
  const SideNav({
    super.key,
    required this.current,
    required this.detailOpen,
    required this.onSelect,
    this.extended = false,
  });

  final AppTab current;
  final bool detailOpen;
  final ValueChanged<AppTab> onSelect;

  /// Показывать подписи рядом с иконками (хватает места в альбоме).
  final bool extended;

  @override
  Widget build(BuildContext context) => Container(
        width: extended ? 176 : 76,
        padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 8),
        decoration: const BoxDecoration(
          color: C.navBg,
          border: Border(right: BorderSide(color: C.dividerSoft)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: EdgeInsets.fromLTRB(extended ? 10 : 0, 8, 0, 16),
              child: Text.rich(
                TextSpan(
                  text: extended ? 'Signal' : 'S',
                  style: T.jost(extended ? 18 : 20),
                  children: [
                    TextSpan(
                      text: extended ? 'AI' : 'AI',
                      style: T.jost(extended ? 18 : 20, color: C.accent),
                    ),
                  ],
                ),
                textAlign: extended ? TextAlign.left : TextAlign.center,
              ),
            ),
            _item(AppTab.ideas, 'Идеи', Icons.navIdeas),
            _item(AppTab.invest, 'Инвест', Icons.navStrategies(C.bg)),
            _item(AppTab.trades, 'Сделки', Icons.navTrades),
            _item(AppTab.strategies, 'Стратегии', Icons.navStrategies(C.bg)),
            _item(AppTab.settings, 'Настройки', Icons.navSettings),
          ],
        ),
      );

  Widget _item(AppTab tab, String label, IconSpec icon) {
    final active = detailOpen ? tab == AppTab.ideas : tab == current;
    final color = active ? C.accent : C.navInactive;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onSelect(tab),
        child: Container(
          constraints: const BoxConstraints(minHeight: 52),
          padding: EdgeInsets.symmetric(horizontal: extended ? 10 : 0, vertical: 8),
          decoration: BoxDecoration(
            color: active ? C.accentFaint : null,
            borderRadius: BorderRadius.circular(R.inner),
          ),
          child: extended
              ? Row(
                  children: [
                    VectorIcon(icon, size: 20, color: color),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(label,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: T.body(12.5, weight: 700, color: color)),
                    ),
                  ],
                )
              : Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    VectorIcon(icon, size: 20, color: color),
                    const SizedBox(height: 3),
                    Text(label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(9, weight: 700, color: color)),
                  ],
                ),
        ),
      ),
    );
  }
}
