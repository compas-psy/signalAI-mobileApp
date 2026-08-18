import 'package:flutter/widgets.dart';

import '../../domain/idea/idea.dart';
import '../../domain/idea/quality_score.dart';
import '../../domain/idea/trade_plan.dart';
import '../../domain/models/strategy.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../layout.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/segmented.dart';
import '../widgets/sparkline.dart';
import '../widgets/strategy_comparison_card.dart';

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
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
            children: [
              CardGrid(
                children: [
                  if (controller.thinMode) const StrategyComparisonCard(),
                  // Методика движка стоит первой: именно по ней считаются
                  // идеи, которые видит владелец. Пакеты ниже — скринер на
                  // устройстве, он ленту больше не наполняет, и путать их
                  // нельзя: экран «Стратегии» показывал только их, из-за чего
                  // читался как список действующих стратегий.
                  const _MethodologyCard(),
                  const _EngineLimitsCard(),
                  if (!controller.thinMode) ...[
                    if (snapshot.packs.isNotEmpty) const _LocalScreenerNote(),
                    for (final pack in snapshot.packs)
                      _PackCard(
                        pack: pack,
                        onToggle: (value) =>
                            controller.toggleStrategy(pack.id, value),
                      ),
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
                    _ParamsCard(snapshot: snapshot),
                    if (snapshot.riskLimits != null)
                      _RiskLimitsCard(limits: snapshot.riskLimits!),
                    if (snapshot.factorEdges.isNotEmpty)
                      _FactorEdgeCard(edges: snapshot.factorEdges),
                  ],
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

/// Стратегии методики §14 — те, по которым движок ищет сетапы.
///
/// Показываются как справка, а не как переключатели: стратегии живут на
/// сервере, и тумблер здесь обещал бы влияние, которого у приложения нет.
/// Доля потока — из ТЗ: отклонение от неё повод для разбора, а не для тихой
/// подгонки. Если разворотов больше половины, движок торгует не то, что
/// заявлено.
class _MethodologyCard extends StatelessWidget {
  const _MethodologyCard();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Методика движка')),
                Text('§14', style: T.mono(11, color: C.faint)),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              'Сетапы ищет сервер. Приложение показывает то, что он нашёл, и '
              'не считает идеи само.',
              style: T.body(11, color: C.muted, height: 1.45),
            ),
            for (final strategy in SetupStrategy.values) ...[
              const SizedBox(height: 10),
              InsetBox(
                padding: const EdgeInsets.fromLTRB(11, 10, 11, 11),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Expanded(
                          child: Text(strategy.label,
                              style: T.body(12.5, weight: 700)),
                        ),
                        Text(
                          '${(strategy.expectedShare * 100).round()}% потока',
                          style: T.mono(11, color: C.accent),
                        ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(strategy.role,
                        style: T.body(11, color: C.muted, height: 1.4)),
                  ],
                ),
              ),
            ],
          ],
        ),
      );
}

/// Пороги и лимиты, которыми движок распоряжается риском (§15.1, §17).
///
/// Числа не декоративные: по ним гаснет кнопка подтверждения и считается
/// объём позиции. Они же разобраны в `docs/TZ_PRIORITY.md` — там, где
/// UX-ТЗ и engine-ТЗ расходились, применено engine-ТЗ как более строгое.
class _EngineLimitsCard extends StatelessWidget {
  const _EngineLimitsCard();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Пороги и риск движка')),
                Text('§15.1 · §17', style: T.mono(11, color: C.faint)),
              ],
            ),
            const SizedBox(height: 10),
            TileGrid(
              minTileWidth: 116,
              // Проценты печатаются с запятой: цифра, которую сравнивают
              // глазами, оформляется по-русски одинаково везде.
              tiles: [
                const MetricTile(
                  label: 'Показывать от',
                  value: '${QualityScore.minimumToShow}',
                  hint: 'ниже — не показываем',
                ),
                const MetricTile(
                  label: 'Торговать от',
                  value: '${QualityScore.minimumToTrade}',
                  hint: 'ниже — наблюдение',
                ),
                const MetricTile(
                  label: 'A-grade от',
                  value: '${QualityScore.aGradeScore}',
                  hint: 'риск 0,75%',
                ),
                const MetricTile(
                  label: 'Риск на сделку',
                  value: '0,50 / 0,75%',
                  hint: 'по оценке идеи',
                ),
                MetricTile(
                  label: 'День',
                  value: _pct(RiskBudget.dailyLossPercent),
                  hint: 'предел убытка',
                ),
                MetricTile(
                  label: 'Неделя',
                  value: _pct(RiskBudget.weeklyLossPercent),
                  hint: 'предел убытка',
                ),
                MetricTile(
                  label: 'Месяц',
                  value: _pct(RiskBudget.monthlyLossPercent),
                  hint: 'предел убытка',
                ),
                MetricTile(
                  label: 'Открытый риск',
                  value: _pct(RiskBudget.openRiskPercent),
                  hint: 'сумма по позициям',
                ),
                MetricTile(
                  label: 'Кластер',
                  value: _pct(RiskBudget.clusterRiskPercent),
                  hint: 'одна группа риска',
                ),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              'Жёсткие лимиты сильнее оценки: высокий балл не пробивает '
              'дневной предел. Числа взяты из engine-ТЗ — там, где оно '
              'расходилось с UX-ТЗ, применено более строгое (docs/TZ_PRIORITY.md).',
              style: T.body(11, color: C.faint, height: 1.45),
            ),
          ],
        ),
      );
}

/// Что такое пакеты ниже и почему их тумблеры не влияют на ленту.
///
/// Скринер на устройстве идеи больше не собирает: оценку §15.1 из
/// одиннадцати компонентов из шести факторов легаси-скринера не составить.
/// Но он остался рабочим инструментом исследования — бэктест и walk-forward
/// гоняются именно им. Без этой оговорки экран читается как список
/// действующих стратегий, а он не он.
class _LocalScreenerNote extends StatelessWidget {
  const _LocalScreenerNote();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Скринер на устройстве')),
                OutlineBadge(
                  label: 'не наполняет ленту',
                  color: C.warning,
                  borderColor: C.warningBorder,
                  background: C.warningFaint,
                  fontWeight: 700,
                  radius: R.pill,
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              'Пакеты ниже — старый скринер, который считал идеи прямо на '
              'телефоне. Ленту идей он больше не наполняет: её считает движок '
              'по методике выше. Скринер остался инструментом исследования — '
              'бэктест и подбор параметров гоняются им, и его тумблеры влияют '
              'только на эти прогоны.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
          ],
        ),
      );
}

/// Процент с запятой: «1.5» → «1,5%».
String _pct(double value) =>
    '${value.toStringAsFixed(value == value.roundToDouble() ? 0 : 1)}%'
        .replaceAll('.', ',');

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
