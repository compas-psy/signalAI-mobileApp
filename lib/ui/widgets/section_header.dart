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
    this.health = DataHealth.full,
    this.healthDetail = '',
    this.onHealth,
    this.trailing,
    this.pillCounts,
  });

  final AppSection section;
  final int pill;
  final ValueChanged<int> onPill;
  final RiskMode mode;

  /// Optional counters rendered next to the stable pill labels. Keeping label
  /// and count as separate Text widgets preserves accessibility/search by the
  /// semantic name while still showing the owner how much is inside.
  final List<int>? pillCounts;

  /// Подпись со временем данных: «данные 12:40 МСК».
  final String? dataAt;

  /// На чём посчитано то, что на экране. Показывается только при деградации:
  /// «всё в порядке» — это состояние по умолчанию, и занимать им место в
  /// шапке значит приучить не замечать чип, когда он наконец загорится.
  final DataHealth health;
  final String healthDetail;
  final VoidCallback? onHealth;

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
                    // Причина прямо под заголовком, а не в диагностике.
                    // Пустая лента без объяснения читается как «приложение
                    // мертво» — именно так владелец её и прочитал.
                    if (health.isDegraded && healthDetail.isNotEmpty) ...[
                      const SizedBox(height: 3),
                      Text(
                        healthDetail,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(
                          10.5,
                          color: health == DataHealth.blind ? C.red : C.warning,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              const SizedBox(width: 10),
              ?trailing,
              if (trailing != null) const SizedBox(width: 8),
              if (health.isDegraded) ...[
                DataHealthChip(health: health, onTap: onHealth),
                const SizedBox(width: 6),
              ],
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
                      count: pillCounts != null && i < pillCounts!.length
                          ? pillCounts![i]
                          : null,
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
  const _Pill({
    required this.label,
    required this.active,
    required this.onTap,
    this.count,
  });

  final String label;
  final int? count;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final color = active ? C.accent : C.muted;
    return GestureDetector(
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
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(label, style: T.body(11.5, weight: 700, color: color)),
            if (count != null) ...[
              const SizedBox(width: 5),
              Text('$count', style: T.mono(10, color: color)),
            ],
          ],
        ),
      ),
    );
  }
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

/// Индикатор здоровья данных: на чём посчитано то, что сейчас на экране.
///
/// Отдельный чип, а не оттенок [RiskModeChip]. `CAUTION` означает «допуск к
/// живым деньгам урезан» — утверждение про риск; молчащая биржа риском не
/// управляет, и подменять одно другим значит врать в обе стороны. Владелец
/// видел пустую ленту при полностью нормальном на вид заголовке и делал
/// единственный доступный вывод: приложение мертво.
class DataHealthChip extends StatelessWidget {
  const DataHealthChip({super.key, required this.health, this.onTap});

  final DataHealth health;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final color = switch (health) {
      DataHealth.full => C.green,
      DataHealth.partial => C.warning,
      DataHealth.blind => C.red,
    };
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 5),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          border: Border.all(color: color.withValues(alpha: 0.35)),
          borderRadius: BorderRadius.circular(R.chip),
        ),
        child: Text(
          health.label.toUpperCase(),
          style: T.body(9.5, weight: 800, color: color),
        ),
      ),
    );
  }
}
