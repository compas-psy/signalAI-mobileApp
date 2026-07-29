import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/portfolio/package_plan.dart';
import '../../domain/portfolio/rebalance.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Цвет класса активов. Один на всё приложение: полоса состава на карточке и
/// та же полоса в разборе обязаны означать одно и то же.
Color classColor(AssetClass assetClass) => switch (assetClass) {
      AssetClass.ofz => C.info,
      AssetClass.corpBonds => C.infoBorder,
      AssetClass.floaters => C.dim,
      AssetClass.gold => C.warning,
      AssetClass.fxBonds => C.tactical,
      AssetClass.dividendStocks => C.greenSoft,
      AssetClass.stocks => C.green,
      AssetClass.moneyMarket => C.muted,
      AssetClass.crypto => C.accent,
    };

/// Полоса целевого состава пакета.
class CompositionBar extends StatelessWidget {
  const CompositionBar({super.key, required this.plan});

  final PackagePlan plan;

  @override
  Widget build(BuildContext context) => ClipRRect(
        borderRadius: BorderRadius.circular(5),
        child: SizedBox(
          height: 10,
          child: Row(
            children: [
              for (final target in plan.targets)
                Expanded(
                  flex: (target.weightPercent * 10).round().clamp(1, 100000),
                  child: ColoredBox(color: classColor(target.assetClass)),
                ),
            ],
          ),
        ),
      );
}

/// Предложение по ребалансировке: конкретные заявки, а не «надо бы».
class RebalanceBlock extends StatelessWidget {
  const RebalanceBlock({super.key, required this.plan});

  final RebalancePlan plan;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(12, 10, 12, 11),
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Ребалансировка'),
            const SizedBox(height: 8),
            if (plan.isEmpty)
              Text(
                'Заявок нет — веса внутри полос. Торговать по мелкому '
                'отклонению значит платить комиссию за иллюзию порядка.',
                style: T.body(11, color: C.muted, height: 1.45),
              )
            else
              for (final order in plan.orders) ...[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                      margin: const EdgeInsets.only(top: 4),
                      width: 7,
                      height: 7,
                      decoration: BoxDecoration(
                        color: order.buy ? C.green : C.red,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 9),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            '${order.buy ? 'Купить' : 'Продать'} '
                            '${order.assetClass.label}'
                            '${order.lots == null ? '' : ' · ${order.lots} лот.'}',
                            style: T.body(11.5, weight: 700),
                          ),
                          Text(order.reason,
                              style: T.body(10.5, color: C.muted, height: 1.35)),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(fmtMoney(order.amount),
                        style: T.mono(11.5,
                            weight: 600, color: order.buy ? C.green : C.red)),
                  ],
                ),
                const SizedBox(height: 8),
              ],
            if (!plan.isEmpty) ...[
              Row(
                children: [
                  Expanded(
                    child: Text('Оборот ребалансировки',
                        style: T.body(11, color: C.muted)),
                  ),
                  Text(fmtMoney(plan.turnover), style: T.mono(11.5, weight: 600)),
                ],
              ),
              const SizedBox(height: 6),
            ],
            for (final note in plan.skipped)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('· $note',
                    style: T.body(10, color: C.faint, height: 1.4)),
              ),
          ],
        ),
      );
}

/// Таблица состава пакета (ТЗ §7).
///
/// Шесть колонок ТЗ — инструмент, роль, текущая доля, целевая, действие,
/// тезис — на телефон в строку не помещаются, поэтому строка складывается в
/// две: сверху инструмент с ролью и долями, снизу действие и тезис. Ни одна
/// колонка при этом не выброшена: «действие» без «почему» — приказ, а
/// «почему» без «действия» — эссе.
class PackageTable extends StatelessWidget {
  const PackageTable({super.key, required this.plan, this.positions});

  final PackagePlan plan;

  /// Фактические веса из книги. null — книга пуста, показываем только цели.
  final List<ClassPosition>? positions;

  @override
  Widget build(BuildContext context) {
    final actual = {
      for (final p in positions ?? const <ClassPosition>[]) p.assetClass: p,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final target in plan.targets) ...[
          _Row(target: target, position: actual[target.assetClass]),
          if (target != plan.targets.last) const SizedBox(height: 9),
        ],
      ],
    );
  }
}

class _Row extends StatelessWidget {
  const _Row({required this.target, required this.position});

  final PackageTarget target;
  final ClassPosition? position;

  /// Что делать с классом. Без фактических весов — «нет данных», а не
  /// «держать»: «держать» читается как проверенный вывод.
  (String, Color) get _action {
    final p = position;
    if (p == null) return ('доля не посчитана', C.faint);
    if (!p.outOfBand) return ('в полосе — не трогаем', C.green);
    return p.drift > 0
        ? ('сократить до ${target.weightPercent.round()}%', C.red)
        : ('докупить до ${target.weightPercent.round()}%', C.accent);
  }

  @override
  Widget build(BuildContext context) {
    final (action, actionColor) = _action;
    final p = position;
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: C.inset,
        borderRadius: BorderRadius.circular(R.inset),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: classColor(target.assetClass),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '${target.assetClass.label} · ${target.assetClass.proxy}',
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: T.body(12, weight: 700),
                ),
              ),
              const SizedBox(width: 8),
              Text(
                p == null
                    ? '${target.weightPercent.round()}%'
                    : '${p.actualPercent.toStringAsFixed(1).replaceAll('.', ',')}% '
                        '→ ${target.weightPercent.round()}%',
                style: T.mono(11, color: C.textSecondary),
              ),
            ],
          ),
          const SizedBox(height: 5),
          Text(action, style: T.body(11, weight: 700, color: actionColor)),
          const SizedBox(height: 3),
          Text(
            target.assetClass.thesis,
            style: T.body(11, color: C.muted, height: 1.4),
          ),
        ],
      ),
    );
  }
}
