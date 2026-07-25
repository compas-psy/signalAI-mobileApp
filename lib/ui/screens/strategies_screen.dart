import 'package:flutter/widgets.dart';

import '../../domain/models/strategy.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/sparkline.dart';

/// Экран «Стратегии»: пакеты, их параметры и бэктест (ТЗ §5.5).
class StrategiesScreen extends StatelessWidget {
  const StrategiesScreen({
    super.key,
    required this.snapshot,
    required this.backtestRunning,
  });

  final StrategiesSnapshot snapshot;
  final bool backtestRunning;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const ScreenHeader(title: 'Стратегии'),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 90),
            children: [
              for (final pack in snapshot.packs) ...[
                _PackCard(
                  pack: pack,
                  onToggle: (value) => controller.toggleStrategy(pack.id, value),
                ),
                const SizedBox(height: 12),
              ],
              _ParamsCard(snapshot: snapshot),
              const SizedBox(height: 12),
              _BacktestCard(
                backtest: snapshot.backtest,
                running: backtestRunning,
                onRun: () => controller.runBacktest(
                  snapshot.packs.isEmpty ? 's1' : snapshot.packs.first.id,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

class _PackCard extends StatelessWidget {
  const _PackCard({required this.pack, required this.onToggle});

  final StrategyPack pack;
  final ValueChanged<bool> onToggle;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.fromLTRB(14, 13, 14, 13),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(pack.name, style: T.body(14, weight: 700)),
                      const SizedBox(height: 3),
                      Text(
                        pack.description,
                        style: T.body(11, color: C.muted, height: 1.4),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                AppToggle(value: pack.enabled, onChanged: onToggle),
              ],
            ),
            const SizedBox(height: 9),
            Text(pack.statsLabel, style: T.mono(10.5, color: C.muted)),
          ],
        ),
      );
}

class _ParamsCard extends StatelessWidget {
  const _ParamsCard({required this.snapshot});

  final StrategiesSnapshot snapshot;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionLabel(snapshot.paramsTitle),
            const SizedBox(height: 6),
            for (final param in snapshot.params)
              KeyValueRow(name: param.name, value: param.value),
          ],
        ),
      );
}

class _BacktestCard extends StatelessWidget {
  const _BacktestCard({
    required this.backtest,
    required this.running,
    required this.onRun,
  });

  final BacktestResult backtest;
  final bool running;
  final VoidCallback onRun;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.fromLTRB(14, 13, 14, 13),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.baseline,
              textBaseline: TextBaseline.alphabetic,
              children: [
                const SectionLabel('Бэктест'),
                const SizedBox(width: 10),
                // Подпись прогона длинная — прижимаем вправо и режем многоточием.
                Expanded(
                  child: Text(
                    running ? 'считаем…' : backtest.info,
                    textAlign: TextAlign.right,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.body(11, color: C.muted),
                  ),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.only(top: 10, bottom: 6),
              child: Sparkline(
                values: backtest.equityCurve,
                height: 56,
                color: running ? C.dim : C.accent,
              ),
            ),
            Row(
              children: [
                for (final stat in backtest.stats) ...[
                  Expanded(
                    child: InsetBox(
                      padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            stat.value,
                            maxLines: 1,
                            style: T.mono(12.5, weight: 600, color: toneColor(stat.tone)),
                          ),
                          const SizedBox(height: 2),
                          Text(stat.label, maxLines: 1, style: T.body(9.5, color: C.muted)),
                        ],
                      ),
                    ),
                  ),
                  if (stat != backtest.stats.last) const SizedBox(width: 8),
                ],
              ],
            ),
            const SizedBox(height: 11),
            Pressable(
              onTap: running ? null : onRun,
              child: Container(
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: running ? C.chip : C.card,
                  border: Border.all(color: C.borderHover),
                  borderRadius: BorderRadius.circular(R.inner),
                ),
                child: Center(
                  child: Text(
                    running ? 'Тестируем 180 дней…' : 'Запустить бэктест',
                    style: T.body(13, weight: 800, color: running ? C.muted : C.accent),
                  ),
                ),
              ),
            ),
          ],
        ),
      );
}
