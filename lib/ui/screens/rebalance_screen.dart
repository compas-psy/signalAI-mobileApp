import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/portfolio/package_plan.dart';
import '../../domain/portfolio/rebalance.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Ребалансировка как отдельное пользовательское действие.
///
/// Раньше пилюля «Ребалансировка» открывала старый обзор капитала. Заголовок
/// обещал список действий, а экран показывал внесено/стоимость/P&L. Здесь
/// выводится именно ответ на вопрос «что купить или продать и почему».
class RebalanceScreen extends StatelessWidget {
  const RebalanceScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final capital = controller.capital;

    if (capital == null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: controller.capitalLoading
              ? const BusyLine(label: 'Считаем текущие веса портфеля…')
              : Text(
                  'Книга капитала недоступна в этом режиме.',
                  textAlign: TextAlign.center,
                  style: T.body(12, color: C.muted, height: 1.5),
                ),
        ),
      );
    }

    if (capital.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Ребалансировать пока нечего', style: T.jost(18)),
              const SizedBox(height: 8),
              Text(
                'Сначала добавьте счёт или синхронизируйте позиции. Без фактических '
                'весов приложение не будет придумывать сделки.',
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
      );
    }

    final plans = controller.packagePlans;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 24),
      children: [
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const SectionLabel('Как читать'),
              const SizedBox(height: 7),
              Text(
                'Сначала используются новые деньги и только потом продажи. '
                'Мелкие отклонения внутри полосы допуска не торгуются: комиссия, '
                'спред и налог могут стоить дороже выравнивания.',
                style: T.body(11.5, color: C.textSecondary, height: 1.5),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        for (final plan in plans) ...[
          _PlanCard(plan: plan, rebalance: controller.rebalance(plan)),
          if (plan != plans.last) const SizedBox(height: 12),
        ],
      ],
    );
  }
}

class _PlanCard extends StatelessWidget {
  const _PlanCard({required this.plan, required this.rebalance});

  final PackagePlan plan;
  final RebalancePlan? rebalance;

  @override
  Widget build(BuildContext context) {
    final result = rebalance;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(plan.riskProfile.label, style: T.jost(17))),
              TagChip(plan.horizon.label),
            ],
          ),
          const SizedBox(height: 5),
          Text(plan.thesis, style: T.body(11, color: C.muted, height: 1.4)),
          const SizedBox(height: 11),
          if (result == null)
            Text(
              'Не удалось сопоставить текущие позиции с классами пакета. '
              'Проверьте контуры операций в книге.',
              style: T.body(11.5, color: C.warning, height: 1.45),
            )
          else if (result.isEmpty) ...[
            Row(
              children: [
                const Expanded(
                  child: Text('Все веса внутри допустимых полос',
                      style: TextStyle(color: C.green)),
                ),
                Text('оборот 0 ₽', style: T.mono(11, color: C.muted)),
              ],
            ),
            if (result.skipped.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                result.skipped.take(3).join('\n'),
                style: T.body(10.5, color: C.faint, height: 1.45),
              ),
            ],
          ] else ...[
            Row(
              children: [
                const Expanded(child: SectionLabel('Предлагаемые действия')),
                Text(
                  'оборот ${fmtMoney(result.turnover)}',
                  style: T.mono(10.5, color: C.muted),
                ),
              ],
            ),
            const SizedBox(height: 8),
            for (var i = 0; i < result.orders.length; i++) ...[
              _OrderRow(order: result.orders[i]),
              if (i != result.orders.length - 1)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 9),
                  child: ColoredBox(
                    color: C.dividerSoft,
                    child: SizedBox(height: 1),
                  ),
                ),
            ],
          ],
        ],
      ),
    );
  }
}

class _OrderRow extends StatelessWidget {
  const _OrderRow({required this.order});

  final RebalanceOrder order;

  @override
  Widget build(BuildContext context) {
    final color = order.buy ? C.green : C.red;
    final action = order.buy ? 'Купить' : 'Продать';
    final instrument = order.instrument ?? order.assetClass.label;
    final quantity = order.lots == null ? '' : ' · ${order.lots} лот.';
    final source = order.fromContribution ? ' · из пополнения' : '';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text('$action $instrument$quantity',
                  style: T.body(12.5, weight: 800, color: color)),
            ),
            Text(fmtMoney(order.amount), style: T.mono(11.5, color: color)),
          ],
        ),
        if (source.isNotEmpty) ...[
          const SizedBox(height: 3),
          Text(source.substring(3), style: T.body(10.5, color: C.info)),
        ],
        const SizedBox(height: 4),
        Text(order.reason, style: T.body(10.5, color: C.muted, height: 1.4)),
      ],
    );
  }
}
