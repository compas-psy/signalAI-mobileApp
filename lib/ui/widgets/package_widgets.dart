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
      AssetClass.bonds => C.info,
      AssetClass.stocks => C.green,
      AssetClass.moneyMarket => C.muted,
      AssetClass.futures => C.tactical,
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
