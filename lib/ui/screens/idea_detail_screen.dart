import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/idea/evidence.dart';
import '../../domain/idea/final_check.dart';
import '../../domain/idea/idea.dart';
import '../../domain/models/settings.dart';
import '../../state/app_controller.dart';
import '../../domain/models/signal.dart';
import '../../domain/position_sizing.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/chart_layers.dart';
import '../widgets/common.dart';
import '../widgets/execution_strip.dart';
import '../widgets/idea_verdict.dart';
import '../widgets/skip_sheet.dart';
import '../widgets/segmented.dart';
import '../widgets/trade_chart.dart';
import '../widgets/vector_icon.dart';

/// Карточка идеи: график, уровни, обоснование, смарт-риск и подтверждение.
///
/// Разбор разложен на три сегмента — «План», «Факторы», «События». Раньше всё
/// это была одна лента карточек, и человек листал мимо того, что ему сейчас не
/// нужно: перед отправкой ордера важен план, а не история фактора.
class IdeaDetailScreen extends StatefulWidget {
  const IdeaDetailScreen({
    super.key,
    required this.signal,
    required this.risk,
    this.showBack = true,
  });

  final TradingSignal signal;
  final RiskProfile risk;

  /// Кнопка «назад» не нужна во второй колонке планшета: список идей никуда
  /// не пропадал, он слева.
  final bool showBack;

  @override
  State<IdeaDetailScreen> createState() => _IdeaDetailScreenState();
}

class _IdeaDetailScreenState extends State<IdeaDetailScreen> {
  int _tab = 0;

  /// Выключенные владельцем слои графика.
  ///
  /// Хранится именно выключенное, а не включённое: набор доступных слоёв
  /// зависит от идеи, и «включённое» пришлось бы пересобирать при каждой
  /// смене — с риском показать слой, за которым нет доказательства.
  final Set<ChartLayer> _hidden = {};

  /// Доказательство, выбранное в тезисе. null — подсветки нет.
  String? _selectedEvidence;

  /// Открыт ли лист пропуска с причиной (ТЗ §12).
  bool _skipping = false;

  @override
  void didUpdateWidget(IdeaDetailScreen old) {
    super.didUpdateWidget(old);
    // На планшете вторая колонка не пересоздаётся при выборе другой идеи —
    // сегмент должен вернуться к плану, а слои к полному набору.
    if (old.signal.id != widget.signal.id) {
      _tab = 0;
      _hidden.clear();
      _selectedEvidence = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final signal = widget.signal;
    final ideas = controller.ideas;
    final idea = ideas.where((i) => i.id == signal.id).firstOrNull;
    final checks = idea == null ? const <CheckResult>[] : _checks(controller, idea);
    final blocked = checks.isNotEmpty && !FinalCheck.passes(checks);
    // Показываем пересечение: слой должен и стоять за доказательством
    // (§9.1), и уметь быть нарисованным.
    final available = {
      for (final layer in idea?.availableLayers ?? TradeChart.allLayers)
        if (TradeChart.renderableLayers.contains(layer)) layer,
    };
    final visible = {
      for (final layer in available)
        if (!_hidden.contains(layer)) layer,
    };
    final highlight = _highlightKeys(idea, _selectedEvidence);
    final execution = controller.execution(signal.id);
    final screen = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _DetailHeader(
          signal: signal,
          onBack: controller.back,
          showBack: widget.showBack,
        ),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 96),
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(R.inner),
                child: DecoratedBox(
                  decoration: BoxDecoration(
                    border: Border.all(color: C.divider),
                    borderRadius: BorderRadius.circular(R.inner),
                  ),
                  child: TradeChart(
                    signal: signal,
                    visibleLayers: visible,
                    highlight: highlight,
                  ),
                ),
              ),
              const SizedBox(height: 10),
              // Без свечей график рисует заглушку, и переключать на нём
              // нечего: панель слоёв там была бы управлением без объекта.
              if (signal.chart != null)
                ChartLayerBar(
                  available: available,
                  visible: visible,
                  onToggle: (layer) => setState(() {
                    _hidden.contains(layer)
                        ? _hidden.remove(layer)
                        : _hidden.add(layer);
                  }),
                ),
              const SizedBox(height: 12),
              if (idea != null)
                ThesisCard(
                  idea: idea,
                  selectedEvidenceId: _selectedEvidence,
                  onSelect: (id) => setState(() {
                    _selectedEvidence = id;
                    // Подсвечивать метку выключенного слоя бессмысленно:
                    // включаем его вместе с подсветкой.
                    if (id != null) {
                      for (final a in idea.annotations) {
                        if (idea.evidence
                            .where((e) => e.id == id)
                            .any((e) => e.annotationIds.contains(a.id))) {
                          _hidden.remove(a.layer);
                        }
                      }
                    }
                  }),
                )
              else
                Text(
                  _lead(signal.note),
                  style: T.body(12.5, color: C.textSecondary, height: 1.5),
                ),
              const SizedBox(height: 12),
              _LevelsRow(signal: signal),
              const SizedBox(height: 12),
              SegmentedControl(
                items: const ['План', 'Оценка', 'События'],
                index: _tab,
                onSelect: (i) => setState(() => _tab = i),
              ),
              const SizedBox(height: 12),
              ...switch (_tab) {
                0 => [
                    _TakeProfitsCard(signal: signal),
                    const SizedBox(height: 12),
                    _SmartRiskCard(signal: signal, risk: widget.risk),
                    const SizedBox(height: 12),
                    if (idea != null) ...[
                      InvalidationCard(idea: idea),
                      const SizedBox(height: 12),
                    ],
                    _ManagementCard(signal: signal),
                  ],
                1 => [
                    if (idea != null)
                      ScoreBreakdownCard(score: idea.score)
                    else
                      _FactorsCard(signal: signal),
                  ],
                _ => [_SignalEventsCard(events: signal.events)],
              },
              const SizedBox(height: 12),
              if (controller.paperAvailable) ...[
                _PaperCard(signal: signal),
                const SizedBox(height: 12),
              ],
              // Пока подтверждения не было — проверка; после него ход
              // исполнения. Показывать проверку поверх открытой позиции
              // значит отвечать на вопрос, который уже не задают.
              if (execution != null) ...[
                ExecutionStrip(execution: execution, now: DateTime.now()),
                const SizedBox(height: 12),
              ] else if (checks.isNotEmpty) ...[
                FinalCheckCard(results: checks),
                const SizedBox(height: 12),
              ],
              if (signal.status.canConfirm)
                Row(
                  children: [
                    Expanded(
                      child: Pressable(
                        pressedScale: .98,
                        // ТЗ §11.1: проваленная или непроверенная проверка не
                        // пускает к деньгам. Кнопка гаснет вместе с причиной
                        // выше, а не молча ничего не делает.
                        onTap: blocked ? null : controller.openSheet,
                        child: Container(
                          padding: const EdgeInsets.all(14),
                          decoration: BoxDecoration(
                            color: blocked ? C.inset : C.accent,
                            borderRadius: BorderRadius.circular(R.button),
                          ),
                          child: Center(
                            child: Text(
                              blocked
                                  ? 'Вход заблокирован проверкой'
                                  : 'Отправить на биржу',
                              style: T.body(14,
                                  weight: 800,
                                  color: blocked ? C.faint : C.onAccent),
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 9),
                    Pressable(
                      // Пропуск без причины журналу бесполезен: ТЗ §12
                      // требует код из справочника. Кнопка ведёт в лист
                      // выбора, а не просто закрывает карточку.
                      onTap: idea != null && controller.skipJournalAvailable
                          ? () => setState(() => _skipping = true)
                          : controller.back,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
                        decoration: BoxDecoration(
                          color: C.card,
                          border: Border.all(color: C.border),
                          borderRadius: BorderRadius.circular(R.button),
                        ),
                        child: Text('Пропустить', style: T.body(14, weight: 700, color: C.muted)),
                      ),
                    ),
                  ],
                )
              else
                Container(
                  padding: const EdgeInsets.all(13),
                  decoration: BoxDecoration(
                    color: C.greenFaint,
                    border: Border.all(color: const Color(0x4D2FD575)),
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Center(
                    child: Text(
                      'В работе · лимитный ордер и OCO (SL + ${signal.takeProfits.length} TP) выставлены',
                      textAlign: TextAlign.center,
                      style: T.body(13, weight: 700, color: C.green),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
    );

    if (idea == null || !_skipping) return screen;
    return Stack(
      children: [
        screen,
        Positioned.fill(
          child: ColoredBox(color: const Color(0x99000000), child: SkipSheet(
            idea: idea,
            onClose: () => setState(() => _skipping = false),
            onSkip: (reason, comment) {
              setState(() => _skipping = false);
              controller.skipIdea(idea, reason: reason, comment: comment);
            },
          )),
        ),
      ],
    );
  }
}

/// Ключи разметки для подсветки выбранного доказательства (ТЗ §9.1).
///
/// Идентификаторы аннотаций строятся как `<id идеи>_<ключ>`; график знает
/// именно хвост — `entry`, `stop`, `tp1`, `zone0`, `break`.
Set<String> _highlightKeys(Idea? idea, String? evidenceId) {
  if (idea == null || evidenceId == null) return const {};
  final evidence =
      idea.evidence.where((e) => e.id == evidenceId).firstOrNull;
  if (evidence == null) return const {};
  final prefix = '${idea.id}_';
  return {
    for (final id in evidence.annotationIds)
      id.startsWith(prefix) ? id.substring(prefix.length) : id,
  };
}

/// Финальная проверка идеи в текущих условиях (ТЗ §11.1).
///
/// Цена берётся из последней котировки сигнала: она приходит строкой уже
/// отформатированной биржевым разбором, и обратный перевод точен — запятая
/// вместо точки и ничего больше.
List<CheckResult> _checks(AppController controller, Idea idea) {
  final center = controller.riskCenter;
  final plan = idea.plan;
  if (center == null || plan == null) return const [];
  return FinalCheck.run(
    idea,
    ExecutionContext(
      now: DateTime.now(),
      lastPrice: _parsePrice(controller.currentSignal?.lastPrice),
      budget: center.budgetFor(idea.score),
      freeMargin: null,
      // Кластер считается после сделки: занятое сейчас плюс то, что займёт
      // эта идея. Проверять «до» бессмысленно — превышение создаём мы.
      clusterRiskAfterPercent:
          center.clusterRisk.usedPercent + plan.riskPercent,
      shownPlanHash: plan.hash,
      paperMode: !controller.liveTradingOn,
    ),
  );
}

double? _parsePrice(String? raw) {
  if (raw == null || raw.isEmpty) return null;
  return double.tryParse(raw.replaceAll(' ', '').replaceAll(',', '.'));
}

/// Первое предложение обоснования — вывод, а не весь абзац.
///
/// Скринер пишет обоснование целиком: сетап, инвалидация, план ведения,
/// происхождение расчёта. Наверху нужен только сетап; остальное дословно
/// показано в сегменте «План», ничего не теряется.
String _lead(String note) {
  final end = note.indexOf('. ');
  return end < 0 ? note : note.substring(0, end + 1);
}

/// Хвост обоснования: всё, кроме первого предложения.
String _tail(String note) {
  final end = note.indexOf('. ');
  return end < 0 ? '' : note.substring(end + 2);
}

/// План ведения сделки — дословно то, что записал скринер.
class _ManagementCard extends StatelessWidget {
  const _ManagementCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) {
    final tail = _tail(signal.note);
    if (tail.isEmpty) return const SizedBox.shrink();
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('План ведения'),
          const SizedBox(height: 8),
          Text(tail, style: T.body(12, color: C.textSecondary, height: 1.55)),
        ],
      ),
    );
  }
}

/// Шесть блоков оценки: имя, сила 1–3 делениями, одна строка объяснения.
class _FactorsCard extends StatelessWidget {
  const _FactorsCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Expanded(child: SectionLabel('Из чего собрана оценка')),
                Text(
                  'конфлюэнс ${signal.score}/100',
                  style: T.body(11, weight: 700, color: C.accent),
                ),
              ],
            ),
            for (final factor in signal.factors) ...[
              const SizedBox(height: 11),
              _FactorRow(factor: factor),
            ],
          ],
        ),
      );
}

/// Ведение идеи на бумаге — отдельно от отправки на биржу.
///
/// Это разные вещи, и раньше их путала одна кнопка: «Подтвердить и
/// исполнить» упиралась в ключи, режим и подтверждение, а завести ту же идею
/// в журнал было нечем — при том что бумажному журналу не нужно ничего из
/// этого. Здесь видно, ведётся ли идея уже, и её можно завести одним нажатием.
class _PaperCard extends StatelessWidget {
  const _PaperCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final note = controller.paperNote(signal);
    return SectionCard(
      margin: EdgeInsets.zero,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('На бумаге'),
          const SizedBox(height: 6),
          Text(
            note == null
                ? 'Идея в журнале не ведётся. Бумажная сделка проживается по '
                    'реальным свечам — без ключей, биржи и подтверждения: '
                    'именно она набирает выборку для допуска к живым деньгам.'
                : 'Ведётся в журнале: $note. Результат считается по реальным '
                    'свечам, уровни задним числом не правятся.',
            style: T.body(11.5, color: note == null ? C.muted : C.green, height: 1.5),
          ),
          if (note == null) ...[
            const SizedBox(height: 10),
            Pressable(
              onTap: controller.trackCurrentSignalOnPaper,
              child: Container(
                padding: const EdgeInsets.symmetric(vertical: 11),
                decoration: BoxDecoration(
                  border: Border.all(color: C.borderHover),
                  borderRadius: BorderRadius.circular(R.inner),
                ),
                child: Center(
                  child: Text(
                    'Вести на бумаге',
                    style: T.body(12.5, weight: 800, color: C.accent),
                  ),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _DetailHeader extends StatelessWidget {
  const _DetailHeader({
    required this.signal,
    required this.onBack,
    this.showBack = true,
  });

  final TradingSignal signal;
  final VoidCallback onBack;
  final bool showBack;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            if (showBack) ...[
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
            ],
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Flexible(
                        child: Text(
                          signal.symbol,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: T.jost(18),
                        ),
                      ),
                      const SizedBox(width: 8),
                      DirectionBadge(
                        label: signal.direction.label,
                        color: directionColor(signal.direction),
                        background: directionBackground(signal.direction),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        '${signal.score}/100',
                        style: T.mono(12, weight: 600, color: C.accent),
                      ),
                    ],
                  ),
                  Text(
                    signal.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.body(11, color: C.muted),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 8),
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(signal.lastPrice, style: T.mono(15, weight: 600)),
                Text(
                  signal.changeLabel,
                  style: T.mono(11, color: signal.changeUp ? C.green : C.red),
                ),
              ],
            ),
          ],
        ),
      );
}

class _LevelsRow extends StatelessWidget {
  const _LevelsRow({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => MetricRow(
        tiles: [
          MetricTile(
            // Вход бывает лимитным (ретест зоны) и стоповым (пробой) — подпись
            // обязана это различать, иначе ордер уйдёт не тот.
            label: signal.entryIsStop ? 'Вход · стоп' : 'Вход · лимит',
            value: fmtPrice(signal.entry, signal.priceDecimals),
            color: C.accent,
          ),
          MetricTile(
            label: 'Стоп-лосс',
            value: fmtPrice(signal.stopLoss, signal.priceDecimals),
            color: C.red,
          ),
          MetricTile(
            label: 'R:R до TP2',
            value: signal.riskReward,
          ),
        ],
      );
}

class _TakeProfitsCard extends StatelessWidget {
  const _TakeProfitsCard({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Тейк-профиты'),
            for (final tp in signal.takeProfits) ...[
              const SizedBox(height: 8),
              Row(
                children: [
                  SizedBox(
                    width: 32,
                    child: Text(tp.label, style: T.mono(11, weight: 600, color: C.green)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      fmtPrice(tp.price, signal.priceDecimals),
                      style: T.mono(13, weight: 600),
                    ),
                  ),
                  Text('${tp.sharePercent}% объёма', style: T.body(11, color: C.muted)),
                  const SizedBox(width: 10),
                  Text(
                    PositionSizing.takeProfitR(signal, tp),
                    style: T.mono(11, color: C.green),
                  ),
                ],
              ),
            ],
          ],
        ),
      );
}

class _FactorRow extends StatelessWidget {
  const _FactorRow({required this.factor});

  final SignalFactor factor;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Expanded(child: Text(factor.name, style: T.body(12, weight: 700))),
              const SizedBox(width: 8),
              StrengthBar(strength: factor.weight),
            ],
          ),
          const SizedBox(height: 3),
          Text(factor.text, style: T.body(11, color: C.muted, height: 1.45)),
        ],
      );
}

/// Смарт-риск: сколько рублей на сделку и какой объём это даёт (ТЗ §6.2).
class _SmartRiskCard extends StatelessWidget {
  const _SmartRiskCard({required this.signal, required this.risk});

  final TradingSignal signal;
  final RiskProfile risk;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const VectorIcon(Icons.shield, size: 15, color: C.accent),
                const SizedBox(width: 8),
                const SectionLabel('Смарт-риск'),
              ],
            ),
            const SizedBox(height: 10),
            MetricRow(
              tiles: [
                MetricTile(
                  label: 'Риск на сделку',
                  value: '${fmt(risk.riskRub, 0)} ₽',
                  hint: '${riskPercentLabel(risk.riskPercent)} от ${fmt(risk.deposit, 0)} ₽',
                ),
                MetricTile(
                  label: 'Объём позиции',
                  value: PositionSizing.quantityLabel(signal, risk),
                  color: C.accent,
                  hint: signal.unitRiskLabel,
                ),
              ],
            ),
            const SizedBox(height: 8),
            // Потенциал сделки в деньгах: сколько принесут все тейки с их
            // долями и сколько заберёт стоп — при рассчитанном объёме.
            MetricRow(
              tiles: [
                MetricTile(
                  label: 'Прибыль по целям',
                  value: '+${fmt(PositionSizing.potentialProfitRub(signal, risk), 0)} ₽',
                  color: C.green,
                  hint: 'все тейки с долями объёма',
                ),
                MetricTile(
                  label: 'Убыток по стопу',
                  value: '−${fmt(PositionSizing.potentialLossRub(signal, risk), 0)} ₽',
                  color: C.red,
                  hint: 'если сработает стоп-лосс',
                ),
              ],
            ),
          ],
        ),
      );
}

class _SignalEventsCard extends StatelessWidget {
  const _SignalEventsCard({required this.events});

  final List<MarketEvent> events;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('События по идее'),
            for (final event in events) ...[
              const SizedBox(height: 9),
              Row(
                children: [
                  Container(
                    width: 7,
                    height: 7,
                    decoration: BoxDecoration(
                      color: impactColor(event.impact),
                      shape: BoxShape.circle,
                    ),
                  ),
                  const SizedBox(width: 10),
                  SizedBox(
                    width: 44,
                    child: Text(event.time, style: T.mono(11, color: C.muted)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(child: Text(event.text, style: T.body(12, color: C.textSoft))),
                ],
              ),
            ],
          ],
        ),
      );
}
