import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/options/execution_ticket.dart';
import '../../domain/options/structures.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';

/// Раздел «Опционы»: цепочка FORTS, конструкции и инструкция к исполнению.
///
/// Приложение не отправляет многоногие заявки: у брокера они исполняются
/// ногами, и частичное исполнение оставляет незакрытый риск. Вместо этого оно
/// делает то, что честнее и полезнее — считает конструкцию по реальной
/// цепочке и выдаёт точный порядок действий для терминала.
class OptionsScreen extends StatefulWidget {
  const OptionsScreen({super.key});

  @override
  State<OptionsScreen> createState() => _OptionsScreenState();
}

class _OptionsScreenState extends State<OptionsScreen> {
  int _asset = 0;
  int _structure = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      AppScope.read(context).loadOptionChain();
    });
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final chain = controller.optionChain;
    const assets = AppController.optionAssets;

    return ListView(
      padding: const EdgeInsets.fromLTRB(18, 12, 18, 90),
      children: [
        if (assets.isNotEmpty)
          SegmentedControl(
            items: assets,
            index: _asset.clamp(0, assets.length - 1),
            onSelect: (i) {
              setState(() {
                _asset = i;
                _structure = 0;
              });
              controller.loadOptionChain(asset: assets[i]);
            },
          ),
        const SizedBox(height: 12),
        if (controller.optionsLoading)
          const _Note(
            title: 'Читаем цепочку',
            text: 'Биржа отдаёт страйки, теоретическую цену, подразумеваемую '
                'волатильность и открытый интерес по каждому контракту.',
          )
        else if (controller.optionsError != null)
          _Note(
            title: 'Цепочка не пришла',
            text: controller.optionsError!,
          )
        else if (chain.isEmpty)
          const _Note(
            title: 'Цепочки нет',
            text: 'Биржа не вернула опционы по этому активу. Это может быть '
                'выходной, отсутствие серии или изменение состава выдачи ISS — '
                'проверить можно в «Контроль → Диагностика данных».',
          )
        else ...[
          _ChainCard(controller: controller),
          const SizedBox(height: 12),
          if (controller.structures.isEmpty)
            const _Note(
              title: 'Конструкций нет',
              text: 'В серии не хватает страйков с ценой, чтобы собрать спред '
                  'с ограниченным риском. Подставлять соседний страйк молча '
                  'нельзя: риск будет не тот, что вы потом выставите.',
            )
          else ...[
            SegmentedControl(
              items: [
                for (final s in controller.structures) _short(s.kind),
              ],
              index: _structure.clamp(0, controller.structures.length - 1),
              onSelect: (i) => setState(() => _structure = i),
            ),
            const SizedBox(height: 12),
            _StructureCard(
              structure: controller
                  .structures[_structure.clamp(0, controller.structures.length - 1)],
              volatility: controller.optionVolatility ?? 0.3,
            ),
          ],
        ],
      ],
    );
  }

  static String _short(StructureKind kind) => switch (kind) {
        StructureKind.bullCallSpread => 'Колл-спред',
        StructureKind.bearPutSpread => 'Пут-спред',
        StructureKind.ironCondor => 'Кондор',
        StructureKind.coveredCall => 'Покрытый',
        StructureKind.protectivePut => 'Защита',
        StructureKind.collar => 'Collar',
      };
}

/// Состояние цепочки: серия, базовый актив, волатильность.
class _ChainCard extends StatelessWidget {
  const _ChainCard({required this.controller});

  final dynamic controller;

  @override
  Widget build(BuildContext context) {
    final chain = controller.optionChain as List<OptionContract>;
    final futures = controller.optionFuturesPrice as double?;
    final volatility = controller.optionVolatility as double?;
    final expiry = chain.isEmpty ? null : chain.first.expiration;

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Серия'),
          const SizedBox(height: 8),
          MetricRow(tiles: [
            MetricTile(
              label: 'Базовый актив',
              value: futures == null ? '—' : fmt(futures, 0),
              hint: controller.optionAsset as String? ?? '',
            ),
            MetricTile(
              label: 'Волатильность',
              value: volatility == null
                  ? '—'
                  : '${(volatility * 100).toStringAsFixed(0)}%',
              color: C.info,
              hint: 'подразумеваемая',
            ),
            MetricTile(
              label: 'До экспирации',
              value: expiry == null
                  ? '—'
                  : '${expiry.difference(DateTime.now()).inDays} дн.',
              hint: 'ближайшая серия',
            ),
          ]),
          const SizedBox(height: 8),
          Text(
            'Страйков в серии: ${chain.length}. Волатильность — медиана '
            'ближних к деньгам страйков: одиночный выброс на мёртвом страйке '
            'не должен двигать всю оценку.',
            style: T.body(10.5, color: C.faint, height: 1.4),
          ),
        ],
      ),
    );
  }
}

/// Конструкция: выплата, метрики, греки и инструкция.
class _StructureCard extends StatelessWidget {
  const _StructureCard({required this.structure, required this.volatility});

  final OptionStructure structure;
  final double volatility;

  @override
  Widget build(BuildContext context) {
    final greeks = structure.greeks(volatility);
    final ticket = ExecutionTicket.of(structure);
    final probability = structure.probabilityOfProfit(volatility);

    return Column(
      children: [
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Expanded(child: Text(structure.kind.label, style: T.jost(17))),
                  OutlineBadge(
                    label: structure.isDebit ? 'ДЕБЕТ' : 'КРЕДИТ',
                    color: structure.isDebit ? C.warning : C.green,
                    borderColor: structure.isDebit ? C.warningBorder : C.greenBorder,
                    background: structure.isDebit ? C.warningFaint : C.greenFaint,
                    fontWeight: 800,
                  ),
                ],
              ),
              const SizedBox(height: 4),
              Text(structure.kind.thesis,
                  style: T.body(11.5, color: C.muted, height: 1.4)),
              const SizedBox(height: 12),
              PayoffChart(structure: structure),
              const SizedBox(height: 12),
              MetricRow(tiles: [
                MetricTile(
                  label: structure.isDebit ? 'Дебет' : 'Кредит',
                  value: fmt(structure.netCashFlow.abs(), 0),
                  color: structure.isDebit ? C.warning : C.green,
                ),
                MetricTile(
                  label: 'Макс. прибыль',
                  value: structure.maxProfit == null
                      ? 'не огр.'
                      : fmt(structure.maxProfit!, 0),
                  color: C.green,
                ),
                MetricTile(
                  label: 'Макс. убыток',
                  value: structure.maxLoss == null
                      ? 'НЕ ОГР.'
                      : fmt(structure.maxLoss!.abs(), 0),
                  color: C.red,
                ),
              ]),
              const SizedBox(height: 8),
              MetricRow(tiles: [
                MetricTile(
                  label: 'Безубыток',
                  value: structure.breakEvens.isEmpty
                      ? '—'
                      : structure.breakEvens.map((b) => fmt(b, 0)).join(' / '),
                ),
                MetricTile(
                  label: 'Вероятность',
                  value: probability == null
                      ? '—'
                      : '${(probability * 100).toStringAsFixed(0)}%',
                  hint: 'ориентир модели',
                ),
                MetricTile(
                  label: 'Дней',
                  value: '${structure.daysToExpiry}',
                  hint: 'до экспирации',
                ),
              ]),
              const SizedBox(height: 12),
              const SectionLabel('Греки'),
              const SizedBox(height: 8),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  _greek('Δ', greeks.delta, 2),
                  _greek('Γ', greeks.gamma, 5),
                  _greek('Θ', greeks.theta, 1, positiveIsGood: true),
                  _greek('V', greeks.vega, 1),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                greeks.theta > 0
                    ? 'Тета положительная: конструкция зарабатывает временем — '
                        'каждый спокойный день работает на вас.'
                    : 'Тета отрицательная: время против конструкции, за каждый '
                        'день простоя платите вы.',
                style: T.body(11, color: greeks.theta > 0 ? C.green : C.warning, height: 1.4),
              ),
            ],
          ),
        ),
        const SizedBox(height: 12),
        _TicketCard(ticket: ticket),
      ],
    );
  }

  Widget _greek(String name, double value, int decimals,
      {bool positiveIsGood = false}) {
    final color = positiveIsGood
        ? (value > 0 ? C.green : C.warning)
        : C.text;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: C.inset,
        borderRadius: BorderRadius.circular(R.chip),
      ),
      child: Text.rich(
        TextSpan(
          text: '$name ',
          style: T.body(11, color: C.muted),
          children: [
            TextSpan(
              text: fmt(value, decimals),
              style: T.mono(11.5, weight: 600, color: color),
            ),
          ],
        ),
      ),
    );
  }
}

/// Инструкция к исполнению — то, ради чего раздел и нужен.
class _TicketCard extends StatelessWidget {
  const _TicketCard({required this.ticket});

  final ExecutionTicket ticket;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Как исполнить'),
          const SizedBox(height: 6),
          Text(
            'Порядок ног обязателен: сначала покупки — они ограничивают риск. '
            'Если сначала продать, а вторая нога не исполнится, в позиции '
            'останется голая продажа.',
            style: T.body(11, color: C.muted, height: 1.45),
          ),
          const SizedBox(height: 12),
          for (final step in ticket.steps) ...[
            Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.fromLTRB(11, 9, 11, 10),
              decoration: BoxDecoration(
                color: C.inset,
                borderRadius: BorderRadius.circular(R.inset),
                border: Border(
                  left: BorderSide(
                    color: step.side.isBuy ? C.green : C.red,
                    width: 3,
                  ),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Text('${step.order}. ', style: T.mono(12, color: C.faint)),
                      Text(
                        step.side.label.toUpperCase(),
                        style: T.body(10, weight: 800,
                            color: step.side.isBuy ? C.green : C.red),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(step.secId,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: T.mono(12.5, weight: 600)),
                      ),
                    ],
                  ),
                  const SizedBox(height: 5),
                  Text(
                    '${step.quantity} шт. · лимит ${fmt(step.limitPrice, 0)}',
                    style: T.mono(11.5, weight: 600, color: C.accent),
                  ),
                  const SizedBox(height: 3),
                  Text(step.reason, style: T.body(10.5, color: C.muted, height: 1.35)),
                ],
              ),
            ),
          ],
          for (final warning in ticket.warnings)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Container(
                    margin: const EdgeInsets.only(top: 5),
                    width: 6,
                    height: 6,
                    decoration: const BoxDecoration(
                      color: C.warning,
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(warning,
                        style: T.body(11, color: C.warning, height: 1.4)),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 6),
          const SectionLabel('После исполнения проверить'),
          const SizedBox(height: 6),
          for (final check in ticket.checks)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text('— $check',
                  style: T.body(11, color: C.textSecondary, height: 1.4)),
            ),
          const SizedBox(height: 10),
          const SectionLabel('Сопровождение'),
          const SizedBox(height: 6),
          for (final rule in ticket.management)
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text('— $rule',
                  style: T.body(11, color: C.textSecondary, height: 1.4)),
            ),
          const SizedBox(height: 12),
          Pressable(
            onTap: () {
              Clipboard.setData(ClipboardData(text: ticket.toPlainText()));
              controller.showToast('Инструкция скопирована');
            },
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 12),
              decoration: BoxDecoration(
                color: C.accent,
                borderRadius: BorderRadius.circular(R.button),
              ),
              child: Center(
                child: Text('Скопировать инструкцию',
                    style: T.body(13, weight: 800, color: C.onAccent)),
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Заявку ставите вы: многоногая заявка исполняется ногами, и '
            'частичное исполнение оставляет незакрытый риск. Приложение не '
            'берёт на себя то, чем не может управлять до конца.',
            style: T.body(10, color: C.faint, height: 1.4),
          ),
        ],
      ),
    );
  }
}

/// Диаграмма выплат конструкции.
class PayoffChart extends StatelessWidget {
  const PayoffChart({super.key, required this.structure, this.height = 150});

  final OptionStructure structure;
  final double height;

  @override
  Widget build(BuildContext context) => Container(
        height: height,
        decoration: BoxDecoration(
          color: C.inset,
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: CustomPaint(
          size: Size.infinite,
          painter: _PayoffPainter(structure),
        ),
      );
}

class _PayoffPainter extends CustomPainter {
  _PayoffPainter(this.structure);

  final OptionStructure structure;

  @override
  void paint(Canvas canvas, Size size) {
    final strikes = [for (final leg in structure.legs) leg.contract.strike]..sort();
    final center = structure.futuresPrice;
    final span = (strikes.last - strikes.first).clamp(center * 0.05, center);
    final low = strikes.first - span * 0.6;
    final high = strikes.last + span * 0.6;

    const steps = 120;
    final values = <double>[];
    for (var i = 0; i <= steps; i++) {
      values.add(structure.payoffAt(low + (high - low) * i / steps));
    }
    final maxValue = values.reduce((a, b) => a > b ? a : b);
    final minValue = values.reduce((a, b) => a < b ? a : b);
    final range = (maxValue - minValue).abs() < 1e-9 ? 1.0 : maxValue - minValue;

    double y(double value) =>
        size.height - 10 - (value - minValue) / range * (size.height - 20);
    double x(int i) => 10 + (size.width - 20) * i / steps;

    // Нулевая линия — граница прибыли и убытка, главный ориентир диаграммы.
    final zeroY = y(0);
    canvas.drawLine(
      Offset(0, zeroY),
      Offset(size.width, zeroY),
      Paint()
        ..color = C.borderStrong
        ..strokeWidth = 1,
    );

    // Заливки: прибыль зелёным, убыток красным — по разные стороны нуля.
    final profit = Path()..moveTo(x(0), zeroY);
    final loss = Path()..moveTo(x(0), zeroY);
    for (var i = 0; i <= steps; i++) {
      final py = y(values[i]);
      (values[i] >= 0 ? profit : loss).lineTo(x(i), py);
      (values[i] >= 0 ? loss : profit).lineTo(x(i), zeroY);
    }
    profit.lineTo(x(steps), zeroY);
    loss.lineTo(x(steps), zeroY);
    canvas
      ..drawPath(profit, Paint()..color = C.greenFaint)
      ..drawPath(loss, Paint()..color = C.redFaint);

    // Сама кривая.
    final line = Path()..moveTo(x(0), y(values.first));
    for (var i = 1; i <= steps; i++) {
      line.lineTo(x(i), y(values[i]));
    }
    canvas.drawPath(
      line,
      Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2
        ..color = C.text,
    );

    // Страйки штрихами: видно, где ломается выплата.
    for (final strike in strikes) {
      final position = 10 + (size.width - 20) * (strike - low) / (high - low);
      canvas.drawLine(
        Offset(position, 6),
        Offset(position, size.height - 6),
        Paint()
          ..color = C.divider
          ..strokeWidth = 1,
      );
    }

    // Текущая цена — жёлтая пунктирная.
    final spotX = 10 + (size.width - 20) * (center - low) / (high - low);
    final dash = Paint()
      ..color = C.accent
      ..strokeWidth = 1.5;
    for (var yPos = 6.0; yPos < size.height - 6; yPos += 8) {
      canvas.drawLine(Offset(spotX, yPos), Offset(spotX, yPos + 4), dash);
    }
  }

  @override
  bool shouldRepaint(_PayoffPainter old) => old.structure != structure;
}

class _Note extends StatelessWidget {
  const _Note({required this.title, required this.text});

  final String title;
  final String text;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: T.jost(16)),
            const SizedBox(height: 6),
            Text(text, style: T.body(11.5, color: C.muted, height: 1.5)),
          ],
        ),
      );
}
