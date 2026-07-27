import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/portfolio/allocation.dart';
import '../../domain/portfolio/package_backtest.dart';
import '../../domain/portfolio/package_plan.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/segmented.dart';
import '../widgets/common.dart';
import '../widgets/package_widgets.dart';
import '../widgets/sparkline.dart';
import '../widgets/vector_icon.dart';

/// Разбор пакета: из чего он состоит и что для этого купить.
///
/// Владелец сформулировал требование прямо: нужно видеть, сколько каждого
/// инструмента в штуках и в деньгах должно быть куплено. Пакет, выраженный
/// процентами, в терминал не выставляется — значит это ещё не план.
class PackageDetailScreen extends StatefulWidget {
  const PackageDetailScreen({super.key, required this.plan});

  final PackagePlan plan;

  @override
  State<PackageDetailScreen> createState() => _PackageDetailScreenState();
}

class _PackageDetailScreenState extends State<PackageDetailScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final controller = AppScope.read(context);
      controller.loadAllocation(widget.plan);
      if (controller.packageHistory(widget.plan.id) == null) {
        controller.loadPackageHistory(widget.plan);
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final plan = widget.plan;
    final allocation = controller.allocation(plan.id);
    final history = controller.packageHistory(plan.id);
    final rebalance = controller.rebalance(plan);

    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: Column(
          children: [
            _Header(
              title: plan.title,
              subtitle: 'горизонт ${plan.horizonYears} лет · '
                  'классов ${plan.targets.length}',
              onBack: () => Navigator.of(context).pop(),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 40),
                children: [
                  SectionCard(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(plan.thesis,
                            style: T.body(12, color: C.textSecondary, height: 1.5)),
                        const SizedBox(height: 12),
                        CompositionBar(plan: plan),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  _CompositionCard(
                    plan: plan,
                    allocation: allocation,
                    loading: controller.allocationLoading(plan.id),
                    onRetry: () => controller.loadAllocation(plan),
                  ),
                  const SizedBox(height: 12),
                  _HistoryCard(
                    history: history,
                    loading: controller.packageLoading(plan.id),
                    onLoad: () => controller.loadPackageHistory(plan),
                  ),
                  if (rebalance != null) ...[
                    const SizedBox(height: 12),
                    SectionCard(child: RebalanceBlock(plan: rebalance)),
                  ],
                  const SizedBox(height: 12),
                  SectionCard(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const SectionLabel('Когда замысел сломан'),
                        const SizedBox(height: 6),
                        Text(plan.invalidation,
                            style: T.body(11.5, color: C.muted, height: 1.5)),
                        const SizedBox(height: 8),
                        Text(
                          'Правило записано заранее не для красоты: без него '
                          'пакет живёт вечно и превращается в кладбище решений, '
                          'каждое из которых когда-то казалось разумным.',
                          style: T.body(10, color: C.faint, height: 1.4),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Состав до инструментов: штуки, лоты и деньги.
class _CompositionCard extends StatelessWidget {
  const _CompositionCard({
    required this.plan,
    required this.allocation,
    required this.loading,
    required this.onRetry,
  });

  final PackagePlan plan;
  final TargetAllocation? allocation;
  final bool loading;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final allocation = this.allocation;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Что купить'),
          const SizedBox(height: 8),
          if (allocation == null)
            loading
                ? const BusyLine(
                    label: 'Спрашиваем цены инструментов на MOEX и Bybit…')
                : Text(
                    'Цены инструментов не получены — количество не посчитано. '
                    'Показывать проценты вместо штук значит выдавать замысел '
                    'за исполнимый план.',
                    style: T.body(11.5, color: C.muted, height: 1.5),
                  )
          else ...[
            Text(
              'Капитал пакета ${fmtMoney(allocation.total)}. Количество '
              'округлено вниз до целого лота: недобор остаётся деньгами, '
              'перебор потребовал бы ещё одной сделки.',
              style: T.body(10.5, color: C.faint, height: 1.45),
            ),
            const SizedBox(height: 10),
            for (final line in allocation.lines) ...[
              _LineRow(line: line),
              const SizedBox(height: 8),
            ],
            Container(
              padding: const EdgeInsets.symmetric(vertical: 8),
              decoration: const BoxDecoration(
                border: Border(top: BorderSide(color: C.divider)),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Text('Остаток кэшем',
                        style: T.body(11.5, color: C.muted)),
                  ),
                  Text(fmtMoney(allocation.residual),
                      style: T.mono(12, weight: 600, color: C.info)),
                ],
              ),
            ),
            Text(
              'Остаток — это то, что не легло в целые лоты. Он не исчезает и '
              'не «размазывается»: иначе сумма строк не сойдётся с капиталом.',
              style: T.body(10, color: C.faint, height: 1.4),
            ),
          ],
          if (!loading) ...[
            const SizedBox(height: 12),
            ActionButton(
              label: allocation == null ? 'Спросить цены' : 'Обновить цены',
              dense: true,
              onTap: onRetry,
            ),
          ],
        ],
      ),
    );
  }
}

/// Строка одного инструмента: цель, количество, факт и что докупить.
class _LineRow extends StatelessWidget {
  const _LineRow({required this.line});

  final AllocationLine line;

  @override
  Widget build(BuildContext context) {
    final delta = line.deltaUnits;
    final deltaValue = line.deltaValue;
    return InsetBox(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: classColor(line.assetClass),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text('${line.symbol} · ${line.assetClass.label}',
                    style: T.body(12.5, weight: 700)),
              ),
              Text('${line.weightPercent.round()}%',
                  style: T.mono(12, weight: 600, color: C.muted)),
            ],
          ),
          const SizedBox(height: 6),
          KeyValueRow(
            name: 'Целевая сумма',
            value: fmtMoney(line.targetValue),
            valueStyle: T.mono(11.5, weight: 600),
          ),
          if (line.reason != null)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Text(line.reason!,
                  style: T.body(10.5, color: C.warning, height: 1.4)),
            )
          else ...[
            KeyValueRow(
              name: 'Цена лота'
                  '${line.quote!.lotSize > 1 ? ' (${line.quote!.lotSize} шт.)' : ''}',
              value: fmtMoney(line.quote!.lotPrice),
              valueStyle: T.mono(11.5),
            ),
            KeyValueRow(
              name: 'Купить лотов',
              value: '${line.lots} · ${line.units} шт.',
              valueStyle: T.mono(11.5, weight: 700, color: C.accent),
            ),
            KeyValueRow(
              name: 'На сумму',
              value: fmtMoney(line.plannedValue!),
              valueStyle: T.mono(11.5, weight: 600),
            ),
            KeyValueRow(
              name: 'Уже есть',
              value: line.actualUnits == 0
                  ? 'нет'
                  : '${line.actualUnits.toStringAsFixed(0)} шт.',
              valueStyle: T.mono(11.5),
              showDivider: delta != null && delta != 0,
            ),
            if (delta != null && delta != 0)
              KeyValueRow(
                name: delta > 0 ? 'Докупить' : 'Сократить',
                value: '${delta.abs()} шт.'
                    '${deltaValue == null ? '' : ' · ${fmtMoney(deltaValue.abs)}'}',
                valueStyle: T.mono(12,
                    weight: 700, color: delta > 0 ? C.green : C.red),
                showDivider: false,
              ),
          ],
        ],
      ),
    );
  }
}

/// Историческая симуляция с кривой капитала.
class _HistoryCard extends StatelessWidget {
  const _HistoryCard({
    required this.history,
    required this.loading,
    required this.onLoad,
  });

  final PackageBacktest? history;
  final bool loading;
  final VoidCallback onLoad;

  @override
  Widget build(BuildContext context) {
    final history = this.history;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Как состав вёл себя раньше'),
          const SizedBox(height: 8),
          if (history == null || history.isEmpty)
            loading
                ? const BusyLine(
                    label: 'Считаем по реальным сериям MOEX и Bybit — '
                        'это несколько запросов истории за пять лет…')
                : Text(
                    'Истории пока нет: серии не пришли либо период слишком '
                    'короткий, чтобы что-то утверждать.',
                    style: T.body(11.5, color: C.muted, height: 1.5),
                  )
          else ...[
            Sparkline(values: history.equity, height: 56, color: C.green),
            const SizedBox(height: 10),
            MetricRow(tiles: [
              MetricTile(
                label: 'Годовых',
                value: '${history.cagr >= 0 ? '+' : '−'}'
                    '${history.cagr.abs().toStringAsFixed(1).replaceAll('.', ',')}%',
                color: history.cagr >= 0 ? C.green : C.red,
                hint: 'за ${history.years} г.',
              ),
              MetricTile(
                label: 'Просадка',
                value: '${history.maxDrawdown.toStringAsFixed(0)}%',
                color: C.red,
                hint: 'максимальная',
              ),
              MetricTile(
                label: 'Худший год',
                value: '${history.worstYearReturn.toStringAsFixed(0)}%',
                color: C.red,
                hint: '${history.worstYear}',
              ),
            ]),
            if (history.stress.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                'Стресс: ${history.stress.entries.map((e) => '${e.key} '
                    '${e.value >= 0 ? '+' : '−'}'
                    '${e.value.abs().toStringAsFixed(0)}%').join(' · ')}',
                style: T.mono(11, color: C.warning),
              ),
            ],
            if (history.missing.isNotEmpty) ...[
              const SizedBox(height: 6),
              Text(
                'Без истории: ${history.missing.join(', ')} — вес '
                'перераспределён по остальным классам',
                style: T.body(10.5, color: C.warning, height: 1.4),
              ),
            ],
            const SizedBox(height: 6),
            Text(
              'История, не прогноз: так этот состав вёл себя с '
              '${history.from.year} по ${history.to.year} год при '
              'ежеквартальной ребалансировке. Будущее он не обещает.',
              style: T.body(10, color: C.faint, height: 1.4),
            ),
          ],
          if (!loading && (history == null || history.isEmpty)) ...[
            const SizedBox(height: 12),
            ActionButton(
              label: 'Посчитать историю',
              dense: true,
              onTap: onLoad,
            ),
          ],
        ],
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({
    required this.title,
    required this.subtitle,
    required this.onBack,
  });

  final String title;
  final String subtitle;
  final VoidCallback onBack;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            Pressable(
              onTap: onBack,
              child: Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: C.card,
                  shape: BoxShape.circle,
                  border: Border.all(color: C.border),
                ),
                child: const Center(
                  child: VectorIcon(Icons.chevronLeft, size: 18, color: C.text),
                ),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: T.jost(18)),
                  Text(subtitle, style: T.body(11, color: C.muted)),
                ],
              ),
            ),
          ],
        ),
      );
}
