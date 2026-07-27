import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../data/ledger/capital_desk.dart';
import '../../domain/ledger/account.dart';
import '../../domain/ledger/ledger_event.dart';
import '../../domain/ledger/money.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../layout.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';
import '../widgets/sparkline.dart';

/// Экран «Сегодня»: состояние капитала и очередь решений.
///
/// Задача — за 5–10 секунд ответить на пять вопросов: сколько всего капитала,
/// что изменилось, сколько под риском, целы ли данные и что решить. Всё
/// показанное — проекция книги; если книги ещё нет, экран говорит именно это,
/// а не рисует нули.
class TodayScreen extends StatelessWidget {
  const TodayScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final state = controller.capital;

    final hasBook = state != null && !state.isEmpty;

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        if (state == null)
          _CapitalPending(controller: controller)
        else if (state.isEmpty)
          _EmptyBook(controller: controller)
        else ...[
          _HeroCard(state: state),
          const SizedBox(height: 12),
          _KpiRow(state: state, controller: controller),
        ],
        const SizedBox(height: 12),
        _DecisionsCard(decisions: controller.decisions),
        const SizedBox(height: 12),
        // Здоровье данных и сверка показываются всегда, включая пустую книгу.
        // Пока этот блок висел на условии «книга не пуста», получался
        // замкнутый круг: наполнить книгу можно было только кнопкой, которая
        // появлялась после наполнения.
        if (hasBook)
          CardGrid(children: [
            _ResultCard(state: state),
            _HealthCard(state: state, controller: controller),
          ])
        else
          _HealthCard(state: state, controller: controller),
      ],
    );
  }
}

/// Совокупный капитал, изменение и аллокация по контурам.
class _HeroCard extends StatelessWidget {
  const _HeroCard({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final allocation = state.allocation();
    final total = state.totalEquity;
    return SectionCard(
      padding: const EdgeInsets.fromLTRB(15, 14, 15, 15),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Совокупный капитал'),
          const SizedBox(height: 6),
          Text(fmtMoney(total), style: T.mono(30, weight: 600)),
          const SizedBox(height: 6),
          Text(
            'внесено ${fmtMoney(state.snapshot.netContributed)} · '
            'результат ${fmtMoney(state.investmentResult, sign: true)}',
            style: T.mono(11.5, color: C.muted),
          ),
          const SizedBox(height: 14),
          _AllocationBar(allocation: allocation),
          const SizedBox(height: 10),
          Wrap(
            spacing: 12,
            runSpacing: 6,
            children: [
              for (final slice in allocation)
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 8,
                      height: 8,
                      decoration: BoxDecoration(
                        color: _contourColor(slice.contour),
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                    const SizedBox(width: 6),
                    Text(slice.contour.label, style: T.body(11, color: C.muted)),
                    const SizedBox(width: 5),
                    Text(
                      '${slice.actualPercent.round()}%',
                      style: T.mono(11, weight: 600),
                    ),
                    Text(
                      ' / цель ${slice.targetPercent.round()}%',
                      style: T.mono(10.5, color: C.faint),
                    ),
                  ],
                ),
            ],
          ),
        ],
      ),
    );
  }
}

Color _contourColor(Contour contour) => switch (contour) {
      Contour.core => C.info,
      Contour.tactical => C.tactical,
      Contour.risk => C.accent,
    };

/// Полоса аллокации: доли контуров одной строкой.
class _AllocationBar extends StatelessWidget {
  const _AllocationBar({required this.allocation});

  final List<ContourAllocation> allocation;

  @override
  Widget build(BuildContext context) {
    final visible = [for (final a in allocation) if (a.actualPercent > 0) a];
    if (visible.isEmpty) {
      return Container(height: 10, decoration: BoxDecoration(
        color: C.inset,
        borderRadius: BorderRadius.circular(5),
      ));
    }
    return ClipRRect(
      borderRadius: BorderRadius.circular(5),
      child: SizedBox(
        height: 10,
        child: Row(
          children: [
            for (final slice in visible)
              Expanded(
                flex: (slice.actualPercent * 10).round().clamp(1, 100000),
                child: ColoredBox(color: _contourColor(slice.contour)),
              ),
          ],
        ),
      ),
    );
  }
}

/// Четыре числа, каждое из которых открывает свой расчёт.
class _KpiRow extends StatelessWidget {
  const _KpiRow({required this.state, required this.controller});

  final CapitalState state;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final pnl = state.snapshot.pnl;
    return Column(
      children: [
        MetricRow(tiles: [
          MetricTile(
            label: 'Свободный кэш',
            value: fmtMoney(state.freeCash),
            color: C.info,
            hint: 'деньги на счетах',
          ),
          MetricTile(
            label: 'В позициях',
            value: fmtMoney(state.positionsValue),
            hint: 'позиций: ${state.snapshot.positions.length}',
          ),
        ]),
        const SizedBox(height: 8),
        MetricRow(tiles: [
          MetricTile(
            label: 'Нереализовано',
            value: fmtMoney(state.unrealized, sign: true),
            color: state.unrealized.isNegative ? C.red : C.green,
            hint: 'по открытым позициям',
          ),
          MetricTile(
            label: 'Чистый результат',
            value: fmtMoney(pnl.net, sign: true),
            color: pnl.net.isNegative ? C.red : C.green,
            hint: 'после комиссий и налогов',
          ),
        ]),
      ],
    );
  }
}

/// Очередь решений — главный блок экрана.
class _DecisionsCard extends StatelessWidget {
  const _DecisionsCard({required this.decisions});

  final List<Decision> decisions;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Очередь решений')),
              if (decisions.isNotEmpty)
                Text('${decisions.length}',
                    style: T.mono(12, weight: 700, color: C.accent)),
            ],
          ),
          const SizedBox(height: 8),
          if (decisions.isEmpty)
            Text(
              'Решений нет: подтверждённых идей не ждёт, позиции защищены, '
              'книга сходится. Пустая очередь — это результат, а не поломка.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            )
          else
            for (final decision in decisions) ...[
              _DecisionRow(
                decision: decision,
                onTap: decision.kind == DecisionKind.idea && decision.target != null
                    ? () {
                        controller.goSection(AppSection.trading);
                        controller.goPill(TradingPill.ideas.index);
                        controller.openSignal(decision.target!);
                      }
                    : null,
              ),
              if (decision != decisions.last) const SizedBox(height: 8),
            ],
        ],
      ),
    );
  }
}

class _DecisionRow extends StatelessWidget {
  const _DecisionRow({required this.decision, this.onTap});

  final Decision decision;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    // Цвет левой грани — смысл, а не украшение: жёлтый зовёт к действию,
    // красный говорит о дефекте, оранжевый — о данных, синий — об учёте.
    final color = switch (decision.kind) {
      DecisionKind.idea => C.accent,
      DecisionKind.expiration => C.warning,
      DecisionKind.reconcile => C.warning,
      DecisionKind.unprotected => C.red,
      DecisionKind.cash => C.info,
      DecisionKind.rebalance => C.tactical,
    };
    final row = Container(
      padding: const EdgeInsets.fromLTRB(11, 10, 11, 10),
      decoration: BoxDecoration(
        color: C.inset,
        borderRadius: BorderRadius.circular(R.inset),
        border: Border(left: BorderSide(color: color, width: 3)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(R.chip),
                ),
                child: Text(decision.kind.label,
                    style: T.body(9, weight: 800, color: color)),
              ),
              const SizedBox(width: 8),
              Text(decision.urgency.label, style: T.body(10, color: C.faint)),
            ],
          ),
          const SizedBox(height: 6),
          Text(decision.title, style: T.body(12.5, weight: 700)),
          const SizedBox(height: 2),
          Text(decision.context, style: T.body(11, color: C.muted, height: 1.4)),
        ],
      ),
    );
    return onTap == null ? row : Pressable(onTap: onTap, child: row);
  }
}

/// Из чего сложился результат: доходы против издержек.
class _ResultCard extends StatelessWidget {
  const _ResultCard({required this.state});

  final CapitalState state;

  @override
  Widget build(BuildContext context) {
    final pnl = state.snapshot.pnl;
    final rows = <(String, Money, Color)>[
      ('Рост стоимости', state.unrealized, C.green),
      ('Реализовано', pnl.realized, C.green),
      ('Доходы: дивиденды, купоны', pnl.income, C.info),
      ('Комиссии и фандинг', -pnl.fees, C.red),
      ('Налоги', -pnl.taxes, C.red),
    ];
    final scale = rows
        .map((r) => r.$2.minor.abs())
        .fold<int>(1, (a, b) => a > b ? a : b);

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Из чего результат'),
          const SizedBox(height: 10),
          for (final row in rows) ...[
            _ResultBar(label: row.$1, value: row.$2, color: row.$3, scale: scale),
            const SizedBox(height: 8),
          ],
          Text(
            'Пополнения и выводы в результат не входят: это перемещение денег '
            'владельца, а не заработок.',
            style: T.body(10, color: C.faint, height: 1.4),
          ),
        ],
      ),
    );
  }
}

class _ResultBar extends StatelessWidget {
  const _ResultBar({
    required this.label,
    required this.value,
    required this.color,
    required this.scale,
  });

  final String label;
  final Money value;
  final Color color;
  final int scale;

  @override
  Widget build(BuildContext context) {
    final fraction = (value.minor.abs() / scale).clamp(0.0, 1.0);
    return Row(
      children: [
        SizedBox(
          width: 132,
          child: Text(label,
              maxLines: 2, style: T.body(10.5, color: C.muted, height: 1.3)),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: ClipRRect(
            borderRadius: BorderRadius.circular(2),
            child: SizedBox(
              height: 6,
              child: Row(
                children: [
                  Expanded(
                    flex: (fraction * 1000).round().clamp(1, 1000),
                    child: ColoredBox(color: value.isZero ? C.border : color),
                  ),
                  Expanded(
                    flex: ((1 - fraction) * 1000).round().clamp(1, 1000),
                    child: const ColoredBox(color: C.inset),
                  ),
                ],
              ),
            ),
          ),
        ),
        const SizedBox(width: 8),
        SizedBox(
          width: 96,
          child: Text(
            fmtMoney(value, sign: true),
            textAlign: TextAlign.right,
            style: T.mono(11, weight: 600, color: value.isZero ? C.muted : color),
          ),
        ),
      ],
    );
  }
}

/// Здоровье данных: сходится ли книга и когда её последний раз сверяли.
///
/// [state] может быть `null` — книга ещё читается или режим без учёта. Даже
/// тогда карточка нужна: в ней живёт единственная кнопка сверки.
class _HealthCard extends StatelessWidget {
  const _HealthCard({required this.state, required this.controller});

  final CapitalState? state;
  final AppController controller;

  @override
  Widget build(BuildContext context) {
    final state = this.state;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Здоровье данных'),
          const SizedBox(height: 8),
          if (state == null)
            _HealthRow(
              ok: false,
              text: controller.capitalLoading
                  ? 'Читаем книгу с диска'
                  : 'Книга ещё не прочитана',
            )
          else ...[
            _HealthRow(
              ok: state.persistent,
              text: state.persistent
                  ? 'Книга пишется на диск'
                  : 'Книга не пишется на диск: операции не переживут перезапуск',
            ),
            _HealthRow(
              ok: state.snapshot.mismatches == 0,
              text: state.snapshot.mismatches == 0
                  ? 'Расхождений с брокером нет'
                  : 'Расхождений: ${state.snapshot.mismatches}',
            ),
            _HealthRow(
              ok: !state.isEmpty,
              text: state.isEmpty
                  ? 'Книга пуста: сверка с площадками наполнит её выпиской'
                  : 'Операций в книге: ${state.snapshot.eventCount}',
            ),
          ],
          _HealthRow(
            ok: controller.riskMode.allowsOpening,
            text: 'Риск-движок: ${controller.riskMode.label} — '
                '${controller.riskMode.hint}',
          ),
          const SizedBox(height: 10),
          ActionButton(
            label: controller.capitalLoading ? 'Сверяем…' : 'Сверить с площадками',
            dense: true,
            onTap: controller.capitalLoading
                ? null
                : () => controller.refreshCapital(sync: true),
          ),
          if (controller.capitalNote != null) ...[
            const SizedBox(height: 8),
            Text(controller.capitalNote!,
                style: T.mono(10, color: C.muted, height: 1.4)),
          ],
        ],
      ),
    );
  }
}

class _HealthRow extends StatelessWidget {
  const _HealthRow({required this.ok, required this.text});

  final bool ok;
  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 7),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 5),
              width: 7,
              height: 7,
              decoration: BoxDecoration(
                color: ok ? C.green : C.warning,
                shape: BoxShape.circle,
              ),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: Text(text, style: T.body(11.5, color: C.textSecondary, height: 1.4)),
            ),
          ],
        ),
      );
}

/// Книга ещё читается.
class _CapitalPending extends StatelessWidget {
  const _CapitalPending({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              controller.capitalLoading ? 'Читаем книгу…' : 'Книга не подключена',
              style: T.jost(17),
            ),
            const SizedBox(height: 6),
            Text(
              controller.capitalLoading
                  ? 'Считаем остатки, позиции и результат по операциям.'
                  : 'В этом режиме учёт капитала недоступен: книга живёт '
                      'только в автономном расчёте на устройстве.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
          ],
        ),
      );
}

/// Книга пуста — это состояние «ещё не заводили», а не ошибка.
class _EmptyBook extends StatelessWidget {
  const _EmptyBook({required this.controller});

  final AppController controller;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Книга пуста', style: T.jost(17)),
            const SizedBox(height: 6),
            Text(
              'Капитал считается из операций, а не вводится числом. Самый '
              'быстрый путь — сверка: приложение заберёт с площадок выписку, '
              'счета и позиции. Банковский резерв и переводы вносятся руками.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
            const SizedBox(height: 12),
            ActionButton(
              label: controller.capitalLoading
                  ? 'Сверяем…'
                  : 'Сверить с площадками',
              primary: true,
              onTap: controller.capitalLoading
                  ? null
                  : () => controller.refreshCapital(sync: true),
            ),
            const SizedBox(height: 8),
            ActionButton(
              label: 'Открыть книгу операций',
              dense: true,
              onTap: () {
                controller.goSection(AppSection.capital);
                controller.goPill(CapitalPill.book.index);
              },
            ),
          ],
        ),
      );
}

/// Спарклайн капитала — заглушка формы, пока истории меньше двух точек.
///
/// Оставлен отдельным виджетом сознательно: как только в книге появятся
/// операции за несколько дней, кривая строится из тех же проекций на даты.
class CapitalSparkline extends StatelessWidget {
  const CapitalSparkline({super.key, required this.values});

  final List<double> values;

  @override
  Widget build(BuildContext context) => values.length < 2
      ? Text('истории пока нет', style: T.body(10.5, color: C.faint))
      : Sparkline(values: values, height: 48, color: C.green);
}
