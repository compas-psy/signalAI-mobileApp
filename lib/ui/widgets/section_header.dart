import 'package:flutter/widgets.dart';

import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';


/// Постоянная шапка раздела: заголовок, время данных, режим движка, пилюли.
///
/// Время данных стоит рядом с заголовком, а не прячется в настройках: число
/// без времени, к которому оно относится, ничего не стоит — особенно когда
/// котировки могли встать полчаса назад.
class SectionHeader extends StatelessWidget {
  const SectionHeader({
    super.key,
    required this.section,
    required this.pill,
    required this.onPill,
    required this.mode,
    this.dataAt,
    this.trailing,
  });

  final AppSection section;
  final int pill;
  final ValueChanged<int> onPill;
  final RiskMode mode;

  /// Подпись со временем данных: «данные 12:40 МСК».
  final String? dataAt;

  final Widget? trailing;

  @override
  Widget build(BuildContext context) {
    final pills = section.pills;
    return Container(
      width: double.infinity,
      padding: EdgeInsets.fromLTRB(S.screen, 14, S.screen, pills.isEmpty ? 10 : 0),
      decoration: const BoxDecoration(
        color: C.headerBg,
        border: Border(bottom: BorderSide(color: C.dividerSoft)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(section.title, style: T.jost(22)),
                    if (dataAt != null) ...[
                      const SizedBox(height: 2),
                      Text(dataAt!, style: T.mono(10.5, color: C.muted)),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 10),
              ?trailing,
              if (trailing != null) const SizedBox(width: 8),
              RiskModeChip(mode: mode),
            ],
          ),
          if (pills.isNotEmpty)
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.only(top: 12, bottom: 10),
              child: Row(
                children: [
                  for (var i = 0; i < pills.length; i++) ...[
                    if (i > 0) const SizedBox(width: 6),
                    _Pill(
                      label: pills[i],
                      active: i == pill,
                      onTap: () => onPill(i),
                    ),
                  ],
                ],
              ),
            ),
        ],
      ),
    );
  }
}

class _Pill extends StatelessWidget {
  const _Pill({required this.label, required this.active, required this.onTap});

  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 34),
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 8),
          decoration: BoxDecoration(
            color: active ? C.accentFaint : C.card,
            border: Border.all(color: active ? C.accentBorder : C.border),
            borderRadius: BorderRadius.circular(R.pill),
          ),
          child: Text(
            label,
            style: T.body(11.5, weight: 700, color: active ? C.accent : C.muted),
          ),
        ),
      );
}

/// Индикатор режима риск-движка. Не переключатель: режим назначает движок.
class RiskModeChip extends StatelessWidget {
  const RiskModeChip({super.key, required this.mode});

  final RiskMode mode;

  @override
  Widget build(BuildContext context) {
    final color = switch (mode) {
      RiskMode.normal => C.green,
      RiskMode.caution => C.warning,
      RiskMode.reduceOnly => C.warning,
      RiskMode.killSwitch => C.red,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        border: Border.all(color: color.withValues(alpha: 0.35)),
        borderRadius: BorderRadius.circular(R.chip),
      ),
      child: Text(mode.label, style: T.body(9.5, weight: 800, color: color)),
    );
  }
}
