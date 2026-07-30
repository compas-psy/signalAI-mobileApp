/// Виджеты пакета, посчитанного движком (§6).
///
/// Экран смотрят, а не читают. Поэтому доля класса — полоса, вес бумаги —
/// полоса, а текст живёт подписью под ними и раскрывается по нажатию, если
/// владелец захотел подробностей. Прежний экран делал наоборот: абзац
/// объяснений вместо одной картинки.
///
/// Отдельно от `package_widgets.dart`: тот обслуживает шаблонные пакеты
/// прежней модели, у которых свой набор классов активов. Свести их в один
/// файл значило бы завести перевод между двумя справочниками — а он не
/// нужен: шаблонные пакеты уходят вместе со старым экраном.
library;

import 'package:flutter/widgets.dart';

import '../../domain/portfolio/package.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';
import 'segmented.dart';

/// Цвет класса активов движка. Один и тот же во всём разделе: полоса
/// состава и точка в легенде обязаны совпадать, иначе легенду надо читать.
Color engineClassColor(String assetClass) => switch (assetClass) {
      'MONEY_MARKET' => C.info,
      'BOND_FUND' => const Color(0xFF4FC3F7),
      'OFZ' => const Color(0xFF7E9CFF),
      'CORPORATE_BOND' => const Color(0xFF9C7BFF),
      'EQUITY' => C.accent,
      'GOLD' => C.warning,
      'CRYPTO_SPOT' || 'CRYPTO_PERPETUAL' => C.tactical,
      _ => C.borderStrong,
    };

String sharePercent(double share, {int decimals = 0}) =>
    '${(share * 100).toStringAsFixed(decimals).replaceAll('.', ',')}%';

/// Полоса состава: доли классов встык, без подписей внутри.
class MixBar extends StatelessWidget {
  const MixBar({super.key, required this.mix, this.height = 12});

  final List<ClassSlice> mix;
  final double height;

  @override
  Widget build(BuildContext context) {
    if (mix.isEmpty) return const SizedBox.shrink();
    return ClipRRect(
      borderRadius: BorderRadius.circular(height / 2),
      child: SizedBox(
        height: height,
        child: Row(
          children: [
            for (final slice in mix)
              Expanded(
                // flex целыми числами: доля 0,075 в пикселях — это 7 или 8,
                // и округление в большую сторону у каждого куска раздувает
                // полосу. Тысячные доли достаточно точны для глаза.
                flex: (slice.weight * 1000).round().clamp(1, 1000),
                child: ColoredBox(color: engineClassColor(slice.assetClass)),
              ),
          ],
        ),
      ),
    );
  }
}

/// Легенда к полосе состава: точка, название класса, доля.
class MixLegend extends StatelessWidget {
  const MixLegend({super.key, required this.mix});

  final List<ClassSlice> mix;

  @override
  Widget build(BuildContext context) => Wrap(
        spacing: 14,
        runSpacing: 6,
        children: [
          for (final slice in mix)
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: engineClassColor(slice.assetClass),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 6),
                Text(slice.label, style: T.body(11.5, color: C.textSecondary)),
                const SizedBox(width: 5),
                Text(sharePercent(slice.weight), style: T.mono(11.5, color: C.text)),
              ],
            ),
        ],
      );
}

/// Строка позиции: вес полосой, тикер, роль. Нажатие раскрывает разбор.
class PositionRow extends StatefulWidget {
  const PositionRow({
    super.key,
    required this.position,
    required this.maxWeight,
  });

  final PackagePosition position;
  final double maxWeight;

  @override
  State<PositionRow> createState() => _PositionRowState();
}

class _PositionRowState extends State<PositionRow> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final position = widget.position;
    final color = engineClassColor(position.assetClass);
    final fill =
        widget.maxWeight <= 0 ? 0.0 : position.weight / widget.maxWeight;
    return Pressable(
      onTap: () => setState(() => _open = !_open),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 7),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SizedBox(
                  width: 72,
                  child: Text(
                    position.symbol,
                    style: T.jost(13, color: C.text, weight: 600),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                Expanded(
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(3),
                    child: SizedBox(
                      height: 6,
                      child: ColoredBox(
                        color: C.inset,
                        child: FractionallySizedBox(
                          alignment: Alignment.centerLeft,
                          widthFactor: fill.clamp(0.03, 1.0),
                          child: ColoredBox(color: color),
                        ),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 48,
                  child: Text(
                    sharePercent(position.weight, decimals: 1),
                    textAlign: TextAlign.right,
                    style: T.mono(12, color: C.text),
                  ),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.only(left: 72, top: 3),
              child: Text(position.role, style: T.body(10.5, color: C.faint)),
            ),
            if (_open) ...[
              const SizedBox(height: 8),
              InsetBox(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (position.title.isNotEmpty &&
                        position.title != position.symbol) ...[
                      Text(position.title, style: T.body(12, color: C.textSoft)),
                      const SizedBox(height: 6),
                    ],
                    Text(
                      position.thesis,
                      style: T.body(11.5, color: C.textSecondary, height: 1.45),
                    ),
                    const SizedBox(height: 8),
                    const SectionLabel('Когда выходим', color: C.faint),
                    const SizedBox(height: 4),
                    Text(
                      position.killConditions,
                      style: T.body(11.5, color: C.muted, height: 1.45),
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

/// Ожидаемая доходность полосой-диапазоном.
///
/// Одно число было бы обманом: доходность оценена по окнам проверки на
/// истории, и разброс между окнами — часть ответа, а не помеха ему.
class ExpectedBand extends StatelessWidget {
  const ExpectedBand({super.key, required this.low, required this.high});

  final double low;
  final double high;

  @override
  Widget build(BuildContext context) {
    final color = high > 0 ? C.green : C.red;
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Text(
          _signed(low),
          style: T.jost(23, color: color, weight: 600),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 4, left: 6, right: 6),
          child: Text('…', style: T.jost(16, color: C.faint)),
        ),
        Text(
          _signed(high),
          style: T.jost(23, color: color, weight: 600),
        ),
        Padding(
          padding: const EdgeInsets.only(bottom: 5, left: 8),
          child: Text('в год', style: T.body(11.5, color: C.faint)),
        ),
      ],
    );
  }

  static String _signed(double value) =>
      '${value >= 0 ? '+' : '−'}${sharePercent(value.abs(), decimals: 1)}';
}

/// Худшее, что уже случалось с составом. Числа, а не выдуманные сценарии.
class StressTiles extends StatelessWidget {
  const StressTiles({super.key, required this.stress});

  final Map<String, String> stress;

  static const _order = ['день', 'месяц', 'квартал', 'год'];

  @override
  Widget build(BuildContext context) {
    final tiles = <Widget>[
      for (final key in _order)
        if (stress[key] != null && stress[key]!.isNotEmpty)
          MetricTile(
            label: 'Худший $key',
            value: stress[key]!.replaceAll('-', '−').replaceAll('.', ','),
            color: stress[key]!.startsWith('-') ? C.red : C.green,
          ),
    ];
    if (tiles.isEmpty) return const SizedBox.shrink();
    return TileGrid(tiles: tiles, minTileWidth: 118);
  }
}
