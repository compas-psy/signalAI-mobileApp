import 'package:flutter/widgets.dart';

import '../../data/api/engine_client.dart';
import '../../state/risk_boost_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Dedicated owner flow for a bounded manual risk override.
///
/// This is deliberately separate from trade confirmation. It chooses only a
/// named server preset, renders the exact signed server preview and can persist
/// that reviewed override. It never calculates a quantity/risk/leverage and it
/// never creates a paper trade or sends an order.
class RiskBoostSheet extends StatelessWidget {
  const RiskBoostSheet({
    super.key,
    required this.controller,
    required this.ideaId,
    required this.symbol,
    required this.currentMode,
    required this.onClose,
  });

  final RiskBoostController controller;
  final String ideaId;
  final String symbol;
  final String currentMode;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: controller,
        builder: (context, _) {
          final preview = controller.preview;
          final result = controller.result;
          final paperOnly = currentMode == 'PAPER';
          return GestureDetector(
            behavior: HitTestBehavior.opaque,
            onTap: _close,
            child: ColoredBox(
              color: const Color(0x99000000),
              child: Column(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  Flexible(
                    child: GestureDetector(
                      onTap: () {},
                      child: SingleChildScrollView(
                        child: Container(
                          width: double.infinity,
                          padding: const EdgeInsets.fromLTRB(18, 16, 18, 22),
                          decoration: const BoxDecoration(
                            color: C.sheet,
                            border: Border(
                              top: BorderSide(color: C.borderStrong),
                            ),
                            borderRadius: BorderRadius.vertical(
                              top: Radius.circular(R.sheet),
                            ),
                          ),
                          child: Column(
                            mainAxisSize: MainAxisSize.min,
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Center(
                                child: Container(
                                  width: 36,
                                  height: 4,
                                  margin: const EdgeInsets.only(bottom: 14),
                                  decoration: BoxDecoration(
                                    color: C.handle,
                                    borderRadius: BorderRadius.circular(2),
                                  ),
                                ),
                              ),
                              Row(
                                children: [
                                  Expanded(
                                    child: Text(
                                      'Рискнуть · $symbol',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: T.jost(18),
                                    ),
                                  ),
                                  const SizedBox(width: 12),
                                  Text(
                                    currentMode,
                                    style: T.body(
                                      11,
                                      weight: 700,
                                      color: C.muted,
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 6),
                              Text(
                                'Вы выбираете только уровень. Риск, объём и '
                                'плечо заново считает сервер в текущих лимитах.',
                                style: T.body(
                                  11.5,
                                  color: C.textSecondary,
                                  height: 1.45,
                                ),
                              ),
                              const SizedBox(height: 13),
                              if (!paperOnly)
                                _Notice(
                                  text: 'Ручное повышение риска пока доступно '
                                      'только в PAPER. В $currentMode параметры '
                                      'не применяются.',
                                  color: C.warning,
                                )
                              else ...[
                                Row(
                                  children: [
                                    Expanded(
                                      child: ActionButton(
                                        label: 'BOOST 1',
                                        dense: true,
                                        onTap: controller.loading
                                            ? null
                                            : () => _load('BOOST_1'),
                                      ),
                                    ),
                                    const SizedBox(width: 9),
                                    Expanded(
                                      child: ActionButton(
                                        label: 'BOOST 2',
                                        dense: true,
                                        onTap: controller.loading
                                            ? null
                                            : () => _load('BOOST_2'),
                                      ),
                                    ),
                                  ],
                                ),
                                if (controller.loading) ...[
                                  const SizedBox(height: 12),
                                  const BusyLine(
                                    label: 'Сервер пересчитывает риск по '
                                        'текущему состоянию…',
                                  ),
                                ],
                                if (preview != null) ...[
                                  const SizedBox(height: 13),
                                  _PreviewCard(preview: preview),
                                ],
                                if (controller.message != null) ...[
                                  const SizedBox(height: 11),
                                  _Notice(
                                    text: controller.message!,
                                    color: controller.needsReview
                                        ? C.warning
                                        : C.green,
                                  ),
                                ],
                                if (controller.error != null) ...[
                                  const SizedBox(height: 11),
                                  _Notice(
                                    text: controller.error!,
                                    color: C.red,
                                  ),
                                ],
                                if (result != null) ...[
                                  const SizedBox(height: 11),
                                  _ResultCard(result: result),
                                ],
                              ],
                              const SizedBox(height: 12),
                              _Notice(
                                text: 'Сделка не создаётся. Это действие только '
                                    'фиксирует проверенные сервером параметры '
                                    'ручного риска; ордер не отправляется.',
                                color: C.accent,
                              ),
                              const SizedBox(height: 14),
                              Row(
                                children: [
                                  if (paperOnly && result == null) ...[
                                    Expanded(
                                      child: ActionButton(
                                        primary: true,
                                        label: _confirmLabel(preview),
                                        onTap: controller.canConfirm
                                            ? () => _confirm()
                                            : null,
                                      ),
                                    ),
                                    const SizedBox(width: 9),
                                  ],
                                  Expanded(
                                    child: ActionButton(
                                      label: result == null ? 'Отмена' : 'Закрыть',
                                      onTap: _close,
                                    ),
                                  ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      );

  String _confirmLabel(RiskPreview? preview) {
    if (preview != null && !preview.allowed) return 'Сервер не разрешает';
    if (controller.needsReview) return 'Подтвердить новые условия';
    return 'Зафиксировать риск';
  }

  void _load(String presetId) {
    controller
        .loadPreset(
          ideaId: ideaId,
          presetId: presetId,
          currentMode: currentMode,
        )
        .catchError((Object _) {});
  }

  void _confirm() {
    controller.confirm().catchError((Object _) {});
  }

  void _close() {
    controller.clear();
    onClose();
  }
}

class _PreviewCard extends StatelessWidget {
  const _PreviewCard({required this.preview});

  final RiskPreview preview;

  @override
  Widget build(BuildContext context) {
    final rows = <(String, String)>[
      ('Авто-риск', _percent(preview.autoRiskPct)),
      ('После BOOST', _percent(preview.effectiveRiskPct)),
      ('Лимит на сделку', _percent(preview.hardCapRiskPct)),
      ('Количество', preview.quantity),
      ('Номинал', preview.notional),
      if (preview.resultingLeverage != null)
        ('Плечо', '${preview.resultingLeverage}×'),
      if (preview.liquidationDistanceRatio != null)
        ('Запас до ликвидации', '${preview.liquidationDistanceRatio}× стопа'),
      ('Открытый риск после', _percent(preview.totalOpenRiskAfter)),
      ('Кластер после', _percent(preview.clusterRiskAfter)),
      ('Убыток при стопе', preview.worstCaseStopLoss),
      ('Ограничение', preview.bindingConstraint),
    ];
    return InsetBox(
      padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 4),
      radius: R.inner,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < rows.length; i++)
            KeyValueRow(
              name: rows[i].$1,
              value: rows[i].$2,
              showDivider: i != rows.length - 1,
            ),
          if (preview.warnings.isNotEmpty) ...[
            const SizedBox(height: 8),
            for (final warning in preview.warnings)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  '⚠ $warning',
                  style: T.body(10.5, color: C.warning, height: 1.35),
                ),
              ),
          ],
          if (preview.blockers.isNotEmpty) ...[
            const SizedBox(height: 8),
            for (final blocker in preview.blockers)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(
                  blocker,
                  style: T.body(
                    10.5,
                    weight: 700,
                    color: C.red,
                    height: 1.35,
                  ),
                ),
              ),
          ],
        ],
      ),
    );
  }
}

class _ResultCard extends StatelessWidget {
  const _ResultCard({required this.result});

  final RiskOverrideResult result;

  @override
  Widget build(BuildContext context) => InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Зафиксировано сервером', color: C.green),
            const SizedBox(height: 6),
            KeyValueRow(
              name: 'Риск',
              value: _percent(result.effectiveRiskPct),
            ),
            KeyValueRow(
              name: 'Количество',
              value: result.effectiveQuantity,
              showDivider: result.effectiveLeverage != null,
            ),
            if (result.effectiveLeverage != null)
              KeyValueRow(
                name: 'Плечо',
                value: '${result.effectiveLeverage}×',
                showDivider: false,
              ),
          ],
        ),
      );
}

class _Notice extends StatelessWidget {
  const _Notice({required this.text, required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 9),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.06),
          border: Border.all(color: color.withValues(alpha: 0.20)),
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Text(
          text,
          style: T.body(11, color: C.textSecondary, height: 1.45),
        ),
      );
}

/// Exact decimal text → percent text without binary floating-point math.
///
/// Examples: 0.005 → 0,5%; 0.00625 → 0,625%; 0.01125 → 1,125%.
String _percent(String raw) {
  var value = raw.trim();
  if (value.isEmpty) return '—';
  var negative = false;
  if (value.startsWith('-')) {
    negative = true;
    value = value.substring(1);
  } else if (value.startsWith('+')) {
    value = value.substring(1);
  }
  final parts = value.split('.');
  if (parts.length > 2 ||
      parts.any((part) => part.isNotEmpty && !_digits(part))) {
    return raw;
  }
  var whole = parts.first.isEmpty ? '0' : parts.first;
  final fraction = parts.length == 2 ? parts[1] : '';
  whole = whole.replaceFirst(RegExp(r'^0+(?=\d)'), '');

  // Remove the source decimal point and move it exactly two places right.
  // Padding preserves leading/trailing zeros without any numeric conversion.
  final canonicalWhole = whole.isEmpty ? '0' : whole;
  final combined = '$canonicalWhole$fraction';
  final targetPoint = canonicalWhole.length + 2;
  final padded = combined.padRight(targetPoint + 1, '0');
  var pctWhole = padded.substring(0, targetPoint);
  final pctFraction =
      padded.substring(targetPoint).replaceFirst(RegExp(r'0+$'), '');
  pctWhole = pctWhole.replaceFirst(RegExp(r'^0+(?=\d)'), '');
  if (pctWhole.isEmpty) pctWhole = '0';
  final text = pctFraction.isEmpty ? pctWhole : '$pctWhole,$pctFraction';
  return '${negative ? '-' : ''}$text%';
}

bool _digits(String value) {
  for (final code in value.codeUnits) {
    if (code < 48 || code > 57) return false;
  }
  return true;
}
