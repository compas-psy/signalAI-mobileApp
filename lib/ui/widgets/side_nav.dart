import 'package:flutter/widgets.dart';

import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'bottom_nav.dart';
import 'vector_icon.dart';

/// Боковая навигация планшета: те же разделы плюс аварийная остановка внизу.
///
/// На широком экране нижняя панель растягивается на всю ширину, и тянуться к
/// её краям неудобно; вертикальная колонка слева держит все разделы в зоне
/// одного движения. Красная кнопка «Стоп» стоит отдельно и внизу: она нужна
/// в момент, когда думать некогда, и не должна теряться среди разделов.
class SideNav extends StatelessWidget {
  const SideNav({
    super.key,
    required this.current,
    required this.onSelect,
    this.extended = false,
    this.onKillSwitch,
    this.killSwitchOn = false,
  });

  final AppSection current;
  final ValueChanged<AppSection> onSelect;

  /// Показывать подписи рядом с иконками (хватает места в альбоме).
  final bool extended;

  final VoidCallback? onKillSwitch;
  final bool killSwitchOn;

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
            for (final item in navItems)
              _item(item.section, item.label, item.icon(C.navBg)),
            const Spacer(),
            if (onKillSwitch != null) _killSwitch(),
          ],
        ),
      );

  Widget _item(AppSection section, String label, IconSpec icon) {
    final active = section == current;
    final color = active ? C.accent : C.navInactive;
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: () => onSelect(section),
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
                        style: T.body(9, weight: 700, color: color)),
                  ],
                ),
        ),
      ),
    );
  }

  Widget _killSwitch() => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onKillSwitch,
        child: Container(
          constraints: const BoxConstraints(minHeight: 48),
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
          decoration: BoxDecoration(
            color: killSwitchOn ? C.red : C.redFaint,
            border: Border.all(color: C.redBorder),
            borderRadius: BorderRadius.circular(R.button),
          ),
          child: Text(
            killSwitchOn ? 'СТОП ВКЛ' : 'СТОП',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: T.body(11, weight: 800, color: killSwitchOn ? C.bg : C.red),
          ),
        ),
      );
}
