import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/risk_center.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import 'ideas_screen.dart';

/// Экран «Сегодня» (ТЗ §6).
///
/// Назначение одно: показать максимум три решения, которые действительно
/// требуют внимания сегодня. Здесь нет ни терминала, ни объяснений, зачем
/// устроено именно так, — только состояние денег, состояние риска и то, что
/// нужно решить.
class TodayScreen extends StatelessWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final now = DateTime.now();
    final ideas = controller.ideas;
    final top = IdeaPriority.top(ideas, now);
    final needDecision = ideas.where((i) => i.state.needsAttention).length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 18),
      children: [
        _CapitalCard(controller: controller),
        const SizedBox(height: S.gap),
        _RiskCard(controller: controller),
        const SizedBox(height: S.gap),
        _DecisionsCard(
          count: needDecision,
          onOpen: () {
            controller.goSection(AppSection.ideas);
            controller.goPill(IdeasPill.decisions.index);
          },
        ),
        if (top.isNotEmpty) ...[
          const SizedBox(height: 18),
          const SectionLabel('Требуют решения'),
          const SizedBox(height: 8),
          for (final idea in top) ...[
            IdeaCard(
              idea: idea,
              now: now,
              onTap: () => controller.openSignal(idea.id),
            ),
            if (idea != top.last) const SizedBox(height: S.gap),
          ],
        ],
        const SizedBox(height: 18),
        _EventCard(controller: controller, now: now),
        const SizedBox(height: S.gap),
        _PortfolioCard(controller: controller),
      ],
    );
  }
}

/// Капитал: инвестиции и торговля (ТЗ §6, блок «Капитал»).
class _CapitalCard extends StatelessWidget {
  const _CapitalCard({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final capital = controller.capital;
    return Pressable(
      onTap: () => controller.goSection(AppSection.portfolio),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Капитал'),
            const SizedBox(height: 8),
            if (capital == null)
              const BusyLine(label: 'Читаем книгу и счета')
            else if (capital.isEmpty)
              Text(
                'Книга пуста: операций ещё не заводили.',
                style: T.body(12, color: C.muted, height: 1.45),
              )
            else ...[
              Text(fmtMoney(capital.totalEquity), style: T.jost(26)),
              const SizedBox(height: 6),
              for (final delta in capital.deltas())
                Padding(
                  padding: const EdgeInsets.only(top: 3),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(delta.label,
                            style: T.body(11.5, color: C.muted)),
                      ),
                      Text(
                        fmtMoney(delta.amount, sign: true),
                        style: T.mono(
                          11.5,
                          color: delta.amount.minor < 0 ? C.red : C.green,
                        ),
                      ),
                    ],
                  ),
                ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Риск дня: сколько лимита израсходовано и сколько осталось (ТЗ §6, §20).
class _RiskCard extends StatelessWidget {
  const _RiskCard({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final center = controller.riskCenter;
    return Pressable(
      onTap: () {
        controller.goSection(AppSection.settings);
        controller.goPill(SettingsPill.risk.index);
      },
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Риск'),
            const SizedBox(height: 8),
            if (center == null)
              Text(
                'Профиль риска не загружен — лимиты посчитать нечем.',
                style: T.body(12, color: C.muted, height: 1.45),
              )
            else ...[
              for (final usage in center.all) ...[
                LimitBar(usage: usage),
                if (usage != center.all.last) const SizedBox(height: 9),
              ],
              if (center.blocked) ...[
                const SizedBox(height: 10),
                Text(
                  '${center.tightest.name}: лимит исчерпан, новые входы '
                  'заблокированы.',
                  style: T.body(11.5, color: C.red, height: 1.45),
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

/// Полоска использования одного лимита.
class LimitBar extends StatelessWidget {
  const LimitBar({super.key, required this.usage});

  final LimitUsage usage;

  @override
  Widget build(BuildContext context) {
    final color = usage.exhausted
        ? C.red
        : usage.fill > 0.66
            ? C.warning
            : C.green;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text('${usage.name} · ${usage.window}',
                  style: T.body(11.5, color: C.muted)),
            ),
            Text(
              '${_pct(usage.usedPercent)} из ${_pct(usage.limitPercent)}',
              style: T.mono(11, color: color),
            ),
          ],
        ),
        const SizedBox(height: 5),
        ClipRRect(
          borderRadius: BorderRadius.circular(R.pill),
          child: SizedBox(
            height: 6,
            child: Stack(
              children: [
                const ColoredBox(color: C.inset, child: SizedBox.expand()),
                FractionallySizedBox(
                  widthFactor: usage.fill,
                  child: ColoredBox(color: color, child: const SizedBox.expand()),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  static String _pct(double v) =>
      '${v.toStringAsFixed(2).replaceAll('.', ',')}%';
}

/// Сколько идей ждёт решения (ТЗ §6: при нуле — честный пустой статус).
class _DecisionsCard extends StatelessWidget {
  const _DecisionsCard({required this.count, required this.onOpen});

  final int count;
  final VoidCallback onOpen;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onOpen,
        child: SectionCard(
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SectionLabel('Нужны решения'),
                    const SizedBox(height: 6),
                    Text(
                      count == 0
                          ? 'Сегодня решать нечего'
                          : 'Идей ждёт ответа: $count',
                      style: T.body(13, weight: 700),
                    ),
                  ],
                ),
              ),
              Text(
                '$count',
                style: T.jost(28, color: count == 0 ? C.faint : C.accent),
              ),
            ],
          ),
        ),
      );
}

/// Ближайшее событие и его влияние (ТЗ §6, блок «Event risk»).
class _EventCard extends StatelessWidget {
  const _EventCard({required this.controller, required this.now});

  final AppController controller;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final events = controller.digest?.events ?? const [];
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Событийный риск'),
          const SizedBox(height: 8),
          if (events.isEmpty)
            Text(
              'Календарь событий не подключён: окна риска не проверяются.',
              style: T.body(12, color: C.muted, height: 1.45),
            )
          else
            for (final event in events.take(2))
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      width: 52,
                      child: Text(event.time, style: T.mono(11, color: C.muted)),
                    ),
                    Expanded(
                      child: Text(event.text,
                          style: T.body(12, height: 1.4)),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}

/// Портфель: ближайший пересмотр (ТЗ §6, блок «Портфель»).
class _PortfolioCard extends StatelessWidget {
  const _PortfolioCard({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final capital = controller.capital;
    final packages = capital?.packages ?? const [];
    return Pressable(
      onTap: () => controller.goSection(AppSection.portfolio),
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Портфель'),
            const SizedBox(height: 8),
            Text(
              packages.isEmpty
                  ? 'Пакет не собран: целевые доли не заданы.'
                  : 'Пакетов в работе: ${packages.length}',
              style: T.body(12, color: packages.isEmpty ? C.muted : C.text, height: 1.45),
            ),
          ],
        ),
      ),
    );
  }
}
