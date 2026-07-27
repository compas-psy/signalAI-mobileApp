import 'package:flutter/widgets.dart';

import '../../domain/models/strategy.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';
import '../widgets/sparkline.dart';

/// Экран «Стратегии»: пакеты, их параметры и бэктест (ТЗ §5.5).
class StrategiesScreen extends StatelessWidget {
  const StrategiesScreen({
    super.key,
    required this.snapshot,
    required this.backtestRunning,
    this.optimizing = false,
    this.backtestStage,
  });

  final StrategiesSnapshot snapshot;
  final bool backtestRunning;

  /// Идёт ли walk-forward подбор параметров.
  final bool optimizing;

  /// Стадия идущего прогона («История SiU6…») — показывается вместо подписи.
  final String? backtestStage;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const ScreenHeader(title: 'Стратегии'),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
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
                optimizing: optimizing,
                stage: backtestStage,
                onRun: () => controller.runBacktest(
                  snapshot.packs.isEmpty ? 's1' : snapshot.packs.first.id,
                ),
                onOptimize: controller.runOptimization,
              ),
              if (snapshot.riskLimits != null) ...[
                const SizedBox(height: 12),
                _RiskLimitsCard(limits: snapshot.riskLimits!),
              ],
              if (snapshot.factorEdges.isNotEmpty) ...[
                const SizedBox(height: 12),
                _FactorEdgeCard(edges: snapshot.factorEdges),
              ],
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

/// Живое состояние риск-лимитов.
///
/// Владельцу важно видеть не декларацию, а факт: сколько сделок открыто, где
/// дневной результат относительно лимита и активна ли пауза. Ровно эти числа
/// применяет риск-движок, когда решает пропустить идею или отклонить.
class _RiskLimitsCard extends StatelessWidget {
  const _RiskLimitsCard({required this.limits});

  final RiskLimitsView limits;

  @override
  Widget build(BuildContext context) {
    final dayR = limits.dayResultR;
    final overLimit = dayR <= -limits.dailyLossLimitR;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Риск-лимиты'),
          const SizedBox(height: 6),
          KeyValueRow(
            name: 'Одновременно в работе',
            value: '${limits.open} из ${limits.maxConcurrent}',
            valueStyle: T.mono(
              12,
              color: limits.open >= limits.maxConcurrent ? C.red : C.text,
            ),
          ),
          KeyValueRow(
            name: 'Результат дня',
            value: '${_signedR(dayR)} из −${_r(limits.dailyLossLimitR)}R',
            valueStyle: T.mono(
              12,
              color: overLimit
                  ? C.red
                  : dayR > 0
                      ? C.green
                      : C.text,
            ),
          ),
          KeyValueRow(
            name: 'Серия стопов',
            value: limits.pauseNote,
            showDivider: false,
            valueStyle: T.mono(
              12,
              color: limits.pauseNote.startsWith('пауза до') ? C.red : C.text,
            ),
          ),
        ],
      ),
    );
  }
}

/// Таблица «фактор → эдж в R»: что из компонент оценки реально работает.
class _FactorEdgeCard extends StatelessWidget {
  const _FactorEdgeCard({required this.edges});

  final List<FactorEdgeRow> edges;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Эдж факторов'),
            const SizedBox(height: 4),
            Text(
              'Разница средней сделки, когда фактор выражен и когда его нет — '
              'по сделкам последнего прогона. Веса по этой таблице пока не '
              'пересчитываются: подгонять их под несколько десятков сделок '
              'значит подгонять под шум.',
              style: T.body(10.5, color: C.muted, height: 1.4),
            ),
            const SizedBox(height: 8),
            for (var i = 0; i < edges.length; i++)
              KeyValueRow(
                name: edges[i].significant
                    ? edges[i].factor
                    : '${edges[i].factor} · мало данных',
                value: '${_signedR(edges[i].edgeR)} '
                    '(${edges[i].withCount}/${edges[i].withoutCount})',
                showDivider: i < edges.length - 1,
                valueStyle: T.mono(
                  12,
                  color: !edges[i].significant
                      ? C.muted
                      : edges[i].edgeR > 0
                          ? C.green
                          : C.red,
                ),
              ),
          ],
        ),
      );
}

String _r(double value) => value.abs().toStringAsFixed(1).replaceAll('.', ',');

String _signedR(double value) =>
    '${value < 0 ? '−' : '+'}${value.abs().toStringAsFixed(2).replaceAll('.', ',')}R';

class _BacktestCard extends StatelessWidget {
  const _BacktestCard({
    required this.backtest,
    required this.running,
    required this.onRun,
    required this.onOptimize,
    this.optimizing = false,
    this.stage,
  });

  final BacktestResult backtest;
  final bool running;
  final bool optimizing;
  final VoidCallback onRun;
  final VoidCallback onOptimize;
  final String? stage;

  bool get busy => running || optimizing;

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
                    busy ? (stage ?? 'считаем…') : backtest.info,
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
            MetricRow(
              tiles: [
                for (final stat in backtest.stats)
                  MetricTile(
                    label: stat.label,
                    value: stat.value,
                    color: toneColor(stat.tone),
                  ),
              ],
            ),
            const SizedBox(height: 11),
            Pressable(
              onTap: busy ? null : onRun,
              child: Container(
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: busy ? C.chip : C.card,
                  border: Border.all(color: C.borderHover),
                  borderRadius: BorderRadius.circular(R.inner),
                ),
                child: Center(
                  child: Text(
                    running ? 'Считаем…' : 'Запустить бэктест',
                    style: T.body(13, weight: 800, color: busy ? C.muted : C.accent),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 8),
            // Walk-forward подбор: train/test-разрез, выбор по данным вне
            // выборки. Сам запускается раз в неделю, кнопка — для «сейчас».
            Pressable(
              onTap: busy ? null : onOptimize,
              child: Container(
                padding: const EdgeInsets.all(11),
                decoration: BoxDecoration(
                  color: busy ? C.chip : C.card,
                  border: Border.all(color: C.borderHover),
                  borderRadius: BorderRadius.circular(R.inner),
                ),
                child: Center(
                  child: Text(
                    optimizing ? 'Подбираем…' : 'Подобрать параметры (walk-forward)',
                    style: T.body(13, weight: 800, color: busy ? C.muted : C.accent),
                  ),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              'Подбор гоняет тот же бэктест по сетке параметров: выбор — по '
              'сделкам вне обучающей выборки, переоптимизация — раз в неделю. '
              'Если дефолт не обыгран, параметры не меняются.',
              style: T.body(10.5, color: C.muted, height: 1.5),
            ),
          ],
        ),
      );
}
