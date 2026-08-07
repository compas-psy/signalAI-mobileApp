import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/idea/risk_center.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';
import 'ideas_screen.dart';

/// «Сегодня» показывает только объекты, которые действительно требуют
/// внимания. В thin-режиме источник идеи перестаёт быть отдельной карточкой,
/// как только по инструменту появилась живая server-side paper-сделка.
class TodayScreen extends StatelessWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final now = DateTime.now();
    final lockedInstruments = controller.thinMode
        ? {
            for (final trade in controller.paperPositions)
              if (trade.status.live) trade.instrumentId,
          }
        : const <String>{};
    final ideas = controller.thinMode
        ? [
            for (final idea in controller.ideas)
              if (idea.state != IdeaState.active &&
                  !lockedInstruments.contains(idea.instrumentId))
                idea,
          ]
        : controller.ideas;
    final top = IdeaPriority.top(ideas, now);
    final needDecision = ideas.where((i) => i.state.needsAttention).length;

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 18),
      children: [
        _Metrics(
          controller: controller,
          needDecision: needDecision,
          onDecisions: () {
            controller.goSection(AppSection.ideas);
            controller.goPill(IdeasPill.decisions.index);
          },
        ),
        const SizedBox(height: 16),
        _IdeasBlock(controller: controller, ideas: top, now: now),
        const SizedBox(height: 16),
        if ((controller.digest?.events.isNotEmpty ?? false) ||
            !(controller.capital?.isEmpty ?? true))
          _SideBySide(
            left: _EventCard(controller: controller, now: now),
            right: _PortfolioCard(controller: controller),
          ),
      ],
    );
  }
}

class _SideBySide extends StatelessWidget {
  const _SideBySide({required this.left, required this.right});

  final Widget left;
  final Widget right;

  static const gap = 12.0;
  static const breakpoint = 640.0;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) => constraints.maxWidth < breakpoint
            ? Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [left, const SizedBox(height: gap), right],
              )
            : Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(child: left),
                  const SizedBox(width: gap),
                  Expanded(child: right),
                ],
              ),
      );
}

class _Metrics extends StatelessWidget {
  const _Metrics({
    required this.controller,
    required this.needDecision,
    required this.onDecisions,
  });

  final AppController controller;
  final int needDecision;
  final VoidCallback onDecisions;

  @override
  Widget build(BuildContext context) {
    final capital = controller.capital;
    final center = controller.riskCenter;
    final daily = center?.daily;
    final delta = capital == null || capital.isEmpty
        ? null
        : (capital.deltas().isEmpty ? null : capital.deltas().first);

    void toPortfolio() => controller.goSection(AppSection.portfolio);
    void toRisk() {
      controller.goSection(AppSection.settings);
      // В thin-порядке Risk — первая пилюля. Старый экран сохраняет
      // исторический enum-index и туда попадает только вне thin-mode.
      controller.goPill(controller.thinMode ? 0 : SettingsPill.risk.index);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TileGrid(
          minTileWidth: 148,
          tiles: [
            MetricTile(
              label: 'Капитал',
              value: capital == null
                  ? '—'
                  : (capital.isEmpty ? 'нет данных' : fmtMoney(capital.totalEquity)),
              hint: capital == null
                  ? 'читаем книгу и счета'
                  : (capital.isEmpty ? 'книга пуста' : 'инвестиции и торговля'),
              onTap: toPortfolio,
            ),
            MetricTile(
              label: delta?.label ?? 'Результат',
              value: delta == null ? '—' : fmtMoney(delta.amount, sign: true),
              color: delta == null
                  ? C.text
                  : (delta.amount.minor < 0 ? C.red : C.green),
              hint: delta == null ? 'нечего считать' : 'по книге операций',
              onTap: toPortfolio,
            ),
            MetricTile(
              label: 'Риск дня',
              value: daily == null ? '—' : _pct(daily.remainingPercent),
              color: daily != null && daily.exhausted ? C.red : C.accent,
              hint: daily == null
                  ? 'профиль не загружен'
                  : 'осталось из ${_pct(daily.limitPercent)} за день',
              onTap: toRisk,
            ),
            MetricTile(
              label: 'Нужны решения',
              value: '$needDecision',
              color: needDecision == 0 ? C.faint : C.accent,
              hint: needDecision == 0 ? 'сегодня решать нечего' : 'ждут ответа',
              onTap: onDecisions,
            ),
          ],
        ),
        if (daily != null) ...[
          const SizedBox(height: 10),
          LimitBar(usage: daily),
        ],
        if (center != null && center.blocked) ...[
          const SizedBox(height: 8),
          Text(
            '${center.tightest.name}: лимит исчерпан, новые входы заблокированы.',
            style: T.body(11.5, color: C.red, height: 1.45),
          ),
        ],
      ],
    );
  }

  static String _pct(double v) => '${v.toStringAsFixed(2).replaceAll('.', ',')}%';
}

class _IdeasBlock extends StatelessWidget {
  const _IdeasBlock({
    required this.controller,
    required this.ideas,
    required this.now,
  });

  final AppController controller;
  final List<Idea> ideas;
  final DateTime now;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Идеи на контроле')),
              Pressable(
                onTap: () => controller.goSection(AppSection.ideas),
                child: Text(
                  'Все идеи →',
                  style: T.body(11.5, weight: 700, color: C.accent),
                ),
              ),
            ],
          ),
          const SizedBox(height: 9),
          if (ideas.isEmpty)
            SectionCard(
              child: Text(
                controller.ideasUnavailableReason ??
                    controller.noSetupsReason ??
                    'Идей, требующих решения, сейчас нет.',
                style: T.body(11.5, color: C.muted, height: 1.5),
              ),
            )
          else
            for (var index = 0; index < ideas.length; index++) ...[
              IdeaCard(
                idea: ideas[index],
                now: now,
                onTap: () => controller.openSignal(ideas[index].id),
              ),
              if (index + 1 < ideas.length) const SizedBox(height: S.gap),
            ],
        ],
      );
}

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
              child: Text(
                '${usage.name} · ${usage.window}',
                style: T.body(11.5, color: C.muted),
              ),
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

  static String _pct(double v) => '${v.toStringAsFixed(2).replaceAll('.', ',')}%';
}

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
                    Expanded(child: Text(event.text, style: T.body(12, height: 1.4))),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}

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
              style: T.body(
                12,
                color: packages.isEmpty ? C.muted : C.text,
                height: 1.45,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
