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
        width: extended ? 176 : 78,
        padding: const EdgeInsets.symmetric(vertical: 16, horizontal: 8),
        decoration: const BoxDecoration(
          color: C.navBg,
          border: Border(right: BorderSide(color: C.dividerSoft)),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Padding(
              padding: EdgeInsets.fromLTRB(extended ? 8 : 0, 0, 0, 14),
              child: Row(
                mainAxisAlignment:
                    extended ? MainAxisAlignment.start : MainAxisAlignment.center,
                children: [
                  const BrandMark(size: 36),
                  if (extended) ...[
                    const SizedBox(width: 9),
                    Text.rich(
                      TextSpan(
                        text: 'Signal',
                        style: T.jost(17),
                        children: [
                          TextSpan(text: 'AI', style: T.jost(17, color: C.accent)),
                        ],
                      ),
                    ),
                  ],
                ],
              ),
            ),
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
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onSelect(tab),
        child: Container(
          constraints: const BoxConstraints(minHeight: 54),
          padding: EdgeInsets.symmetric(horizontal: extended ? 10 : 0, vertical: 9),
          decoration: BoxDecoration(
            color: active ? C.accentFaint : null,
            borderRadius: BorderRadius.circular(R.button),
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
                    const SizedBox(height: 4),
                    Text(label,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(10, weight: 700, color: color)),
                  ],
                ),
        ),
      ),
    );
  }
}
