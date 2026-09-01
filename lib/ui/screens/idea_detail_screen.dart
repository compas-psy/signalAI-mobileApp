import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../data/api/live_idea_source.dart';
import '../../domain/idea/evidence.dart';
import '../../domain/idea/final_check.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/models/settings.dart';
import '../../state/app_controller.dart';
import '../../domain/models/signal.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/execution_strip.dart';
import '../widgets/idea_chart_card.dart';
import '../widgets/idea_head.dart';
import '../widgets/idea_verdict.dart';
import '../widgets/plan_card.dart';
import '../widgets/segmented.dart';
import '../widgets/skip_sheet.dart';
import '../widgets/trade_chart.dart';
import '../widgets/vector_icon.dart';

/// Карточка идеи по прототипу `design/prototype_v21.html` (`renderIdeaDetail`).
///
/// Разбор — одна лента сверху вниз: шапка с состоянием и оценкой, график с
/// разметкой, тезис рядом с параметрами сделки, разбор оценки, событийный
/// риск рядом с условиями отмены, и внизу липкая полоса подтверждения.
///
/// До этого те же данные лежали под сегментами «План · Оценка · События».
/// Сегменты убраны намеренно: решение об отправке ордера принимается по всем
/// трём сразу, а переключатель заставлял держать в голове то, что скрыто на
/// соседней вкладке. Плотность вместо этого снимает раскладка — на широком
/// экране тезис и план стоят рядом, а не друг под другом.
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
  /// Выключенные владельцем слои графика.
  ///
  /// Хранится именно выключенное, а не включённое: набор доступных слоёв
  /// зависит от идеи, и «включённое» пришлось бы пересобирать при каждой
  /// смене — с риском показать слой, за которым нет доказательства.
  final Set<ChartLayer> _hidden = {};

  /// Доказательство, выбранное в тезисе. null — подсветки нет.
  String? _selectedEvidence;

  /// Последний доказанный сервером путь рынка после сигнала.
  ///
  /// Это не часть immutable-идеи: оценка и план остаются снимком момента
  /// рождения, а это отдельный execution/admission verdict «можно ли входить
  /// сейчас». Ненулевой поздний verdict не снимается из-за временного сбоя
  /// сети — только при выборе другой идеи.
  IdeaMarketProgress? _marketProgress;

  /// Открыт ли лист пропуска с причиной (ТЗ §12).
  bool _skipping = false;

  @override
  void didUpdateWidget(IdeaDetailScreen old) {
    super.didUpdateWidget(old);
    // На планшете вторая колонка не пересоздаётся при выборе другой идеи —
    // слои должны вернуться к полному набору, подсветка сняться.
    if (old.signal.id != widget.signal.id) {
      _hidden.clear();
      _selectedEvidence = null;
      _marketProgress = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final signal = widget.signal;
    final now = DateTime.now();
    final idea = controller.ideas.where((i) => i.id == signal.id).firstOrNull;
    // В production окончательный допуск атомарно проверяет сервер в
    // approve-paper. Повторять его по неполному мобильному снимку означало
    // получать второй, расходящийся вердикт. Локальная проверка остаётся
    // только в демо-контуре.
    final checks = idea == null || !controller.demoData
        ? const <CheckResult>[]
        : _checks(controller, idea);
    final lateEntry = _marketProgress?.blocksNewEntry == true;
    // Проверка способна заблокировать только готовый вход. У идеи, которая
    // ещё ждёт триггера, входа нет по определению: красный вердикт здесь
    // подменял честное «наблюдать» сообщением о несуществующей сделке.
    //
    // Live market progress — исключение: если рынок уже прошёл исходный план,
    // это отдельный execution-verdict и он имеет приоритет над старой оценкой.
    final blocked = idea != null &&
        idea.readiness.canAct &&
        (lateEntry ||
            (checks.isNotEmpty && !FinalCheck.passes(checks)) ||
            (!controller.demoData && !idea.canApprovePaper));
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
    final execution = controller.execution(signal.id);
    // Свечи движка тянутся при первом показе разбора: до этого график идеи
    // не наполнялся ничем, потому что идеи приходят с сервера, а свечи умел
    // строить только скринер на устройстве.
    if (idea != null &&
        controller.ideaChart(idea.id) == null &&
        !controller.ideaChartFailed(idea)) {
      controller.loadIdeaChart(idea);
    }

    final screen = Stack(
      children: [
        ListView(
          // Нижнее поле держит место под липкую полосу подтверждения: иначе
          // последняя карточка ленты навсегда остаётся под ней.
          padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 132),
          children: [
            if (widget.showBack) ...[
              _BackButton(onTap: controller.back),
              const SizedBox(height: 12),
            ],
            IdeaDetailHead(signal: signal, idea: idea, now: now),
            // Чем закончилась идея — до графика, а не после.
            //
            // Владелец открывает разбор, видит цену у третьей цели и первым
            // делом спрашивает: это ещё сделка или уже история. Отвечать
            // обязан экран, а не арифметика в голове по картинке.
            if (idea != null && idea.closingReason.isNotEmpty) ...[
              const SizedBox(height: 12),
              _ClosedNote(state: idea.state, reason: idea.closingReason),
            ],
            const SizedBox(height: 14),
            IdeaChartCard(
              signal: signal,
              idea: idea,
              chart: idea == null ? null : controller.ideaChart(idea.id),
              timeframe: idea == null ? '' : controller.ideaTimeframe(idea),
              onTimeframe: idea == null
                  ? null
                  : (tf) => controller.selectIdeaTimeframe(idea, tf),
              loading: idea != null && controller.ideaChartLoading(idea),
              failed: idea != null && controller.ideaChartFailed(idea),
              failureReason:
                  idea == null ? '' : controller.ideaChartFailureReason(idea),
              available: available,
              visible: visible,
              highlight: _highlightKeys(idea, _selectedEvidence),
              onToggle: (layer) => setState(() {
                _hidden.contains(layer)
                    ? _hidden.remove(layer)
                    : _hidden.add(layer);
              }),
              onProgress: (progress) {
                if (!mounted) return;
                final previous = _marketProgress;
                if (previous?.status == progress.status &&
                    previous?.asOf == progress.asOf &&
                    previous?.summary == progress.summary) {
                  return;
                }
                setState(() => _marketProgress = progress);
              },
            ),
            const SizedBox(height: 12),
            _Pair(
              left: idea == null
                  ? _PlainThesis(note: signal.note)
                  : ThesisCard(
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
                    ),
              right: PlanCard(signal: signal, idea: idea, risk: widget.risk),
            ),
            const SizedBox(height: 12),
            if (idea != null)
              ScoreBreakdownCard(score: idea.score)
            else
              _FactorsCard(signal: signal),
            const SizedBox(height: 12),
            _Pair(
              left: _EventRiskCard(signal: signal, idea: idea, now: now),
              right: idea != null
                  ? InvalidationCard(idea: idea)
                  : _PlainInvalidation(signal: signal),
            ),
            // Пока подтверждения не было — проверка; после него ход
            // исполнения. Показывать проверку поверх открытой позиции значит
            // отвечать на вопрос, который уже не задают.
            if (execution != null) ...[
              const SizedBox(height: 12),
              ExecutionStrip(execution: execution, now: now),
            ] else if (checks.isNotEmpty) ...[
              const SizedBox(height: 12),
              FinalCheckCard(results: checks),
            ],
            if (controller.paperAvailable && idea == null) ...[
              const SizedBox(height: 12),
              _PaperCard(signal: signal),
            ],
          ],
        ),
        Positioned(
          left: S.screen,
          right: S.screen,
          bottom: 12,
          child: _ConfirmBar(
            signal: signal,
            state: idea?.state,
            readiness: idea?.readiness,
            paperOnly: idea != null && !controller.demoData,
            blocked: blocked,
            lateEntry: lateEntry,
            lateReason: lateEntry ? (_marketProgress?.summary ?? '') : '',
            onConfirm: controller.openSheet,
            onSkip: idea != null && controller.canRejectIdea(idea)
                ? () => setState(() => _skipping = true)
                : controller.back,
          ),
        ),
      ],
    );

    if (idea == null || !_skipping) return screen;
    return Stack(
      children: [
        screen,
        Positioned.fill(
          child: ColoredBox(
            color: const Color(0x99000000),
            child: SkipSheet(
              idea: idea,
              onClose: () => setState(() => _skipping = false),
              onSkip: (reason, comment) {
                setState(() => _skipping = false);
                controller.skipIdea(idea, reason: reason, comment: comment);
              },
            ),
          ),
        ),
      ],
    );
  }
}

/// Две карточки рядом, когда хватает ширины, и колонкой, когда не хватает.
///
/// Прототип ставит тезис рядом с планом и событийный риск рядом с отменой —
/// это пары, которые читаются вместе. На телефоне пара разворачивается в
/// колонку: две карточки по 170 пикселей не читаются вовсе.
class _Pair extends StatelessWidget {
  const _Pair({required this.left, required this.right});

  final Widget left;
  final Widget right;

  static const gap = 12.0;

  /// Ширина, с которой пара встаёт в две колонки.
  ///
  /// Прототип переводит `idea-summary-grid` в одну колонку при экране ≤1180,
  /// где под разбор остаётся около 780 пикселей. Порог здесь именно про
  /// разбор, а не про экран: на планшете он живёт во второй колонке.
  static const breakpoint = 720.0;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth < breakpoint) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [left, const SizedBox(height: gap), right],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(flex: 6, child: left),
              const SizedBox(width: gap),
              Expanded(flex: 5, child: right),
            ],
          );
        },
      );
}

/// Возврат к списку идей (прототип: `back-mobile`).
class _BackButton extends StatelessWidget {
  const _BackButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Align(
        alignment: Alignment.centerLeft,
        child: Pressable(
          onTap: onTap,
          child: Container(
            padding: const EdgeInsets.fromLTRB(10, 8, 13, 8),
            decoration: BoxDecoration(
              color: C.card,
              border: Border.all(color: C.border),
              borderRadius: BorderRadius.circular(R.button),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const VectorIcon(Icons.chevronLeft,
                    size: 15, color: C.textSecondary),
                const SizedBox(width: 6),
                Text('К списку идей',
                    style: T.body(12,
                        weight: 700, color: C.textSecondary)),
              ],
            ),
          ),
        ),
      );
}

/// Липкая полоса подтверждения (прототип: `sticky-confirm`).
///
/// Раньше кнопки стояли последней строкой ленты: чтобы подтвердить план,
/// приходилось долистать разбор до конца, а чтобы понять, почему кнопка
/// погасла, — вернуться к проверке. Полоса всегда на экране и всегда говорит,
/// что произойдёт по нажатию.
class _ConfirmBar extends StatelessWidget {
  const _ConfirmBar({
    required this.signal,
    required this.state,
    required this.readiness,
    required this.paperOnly,
    required this.blocked,
    required this.lateEntry,
    required this.lateReason,
    required this.onConfirm,
    required this.onSkip,
  });

  final TradingSignal signal;

  /// Состояние идеи движка. null — разбор не приехал, идём по статусу сигнала.
  final IdeaState? state;

  final IdeaReadiness? readiness;

  /// Серверная идея в этой поставке допускает только paper-решение.
  final bool paperOnly;

  /// Финальная проверка не пускает к деньгам (ТЗ §11.1).
  final bool blocked;

  /// Рынок уже прошёл исходный вход/цели: старый score больше не является
  /// разрешением на новую сделку.
  final bool lateEntry;
  final String lateReason;

  final VoidCallback onConfirm;
  final VoidCallback onSkip;

  bool get _working => state == IdeaState.active || signal.status.isWorking;

  bool get _canConfirm => state == null
      ? signal.status.canConfirm
      : readiness?.canAct == true &&
          state!.canConfirm &&
          signal.status.canConfirm;

  bool get _canSkip => state != null && !state!.isTerminal && !_working;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(11),
        decoration: BoxDecoration(
          color: C.sheet,
          border: Border.all(color: C.borderStrong),
          borderRadius: BorderRadius.circular(R.card),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(_headline(),
                style: T.body(12.5,
                    weight: 800, color: _headlineColor())),
            const SizedBox(height: 3),
            Text(_sub(), style: T.body(10.5, color: C.faint, height: 1.4)),
            if (!_working && (_canConfirm || _canSkip)) ...[
              const SizedBox(height: 10),
              if (_canConfirm)
                Row(
                  children: [
                    Expanded(
                      child: ActionButton(
                        label: lateEntry
                            ? 'Вход запрещён'
                            : blocked
                                ? (paperOnly
                                    ? 'Вход заблокирован сервером'
                                    : 'Вход заблокирован проверкой')
                                : (paperOnly
                                    ? 'Подтвердить paper-сделку'
                                    : 'Подтвердить план'),
                        primary: true,
                        onTap: !blocked ? onConfirm : null,
                      ),
                    ),
                    const SizedBox(width: 9),
                    SizedBox(
                      width: 118,
                      child:
                          ActionButton(label: 'Пропустить', onTap: onSkip),
                    ),
                  ],
                )
              else
                Align(
                  alignment: Alignment.centerRight,
                  child: SizedBox(
                    width: 150,
                    child: ActionButton(label: 'Убрать идею', onTap: onSkip),
                  ),
                ),
            ],
          ],
        ),
      );

  String _headline() {
    if (_working) return 'Позиция сопровождается автоматически';
    if (lateEntry) return 'Сделка упущена · вход запрещён';
    if (blocked) return 'Проверка перед входом не пройдена';
    if (readiness == IdeaReadiness.waiting) {
      return 'Наблюдать · ждём триггер';
    }
    if (readiness == IdeaReadiness.active) return 'Можно действовать';
    return switch (state) {
      IdeaState.watch => 'Ждём подтверждения структуры',
      IdeaState.ready => 'Зона известна, триггер ещё не сработал',
      IdeaState.expired => 'Срок сигнала истёк — исполнять нельзя',
      IdeaState.invalidated => 'Замысел сломан kill-условием',
      IdeaState.skipped => 'Идея пропущена, причина записана в журнал',
      IdeaState.closed => 'Сделка закрыта',
      _ => 'После одного подтверждения сопровождение будет автоматическим',
    };
  }

  Color _headlineColor() {
    if (_working) return C.green;
    if (lateEntry || blocked) return C.red;
    return _canConfirm ? C.text : C.textSecondary;
  }

  String _sub() {
    if (_working) {
      if (!paperOnly) {
        return 'Лимитный ордер и OCO (стоп + '
            '${signal.takeProfits.length} TP) выставлены на бирже.';
      }
      return 'Paper-сделку ведёт сервер: вход, стоп и '
          '${signal.takeProfits.length} цели проверяются без телефона.';
    }
    if (lateEntry) {
      return lateReason.isEmpty
          ? 'Рынок уже прошёл исходный план. Новый вход запрещён.'
          : lateReason;
    }
    if (!_canConfirm) {
      return 'Это контекст, а не подтверждённый вход. Кнопка сделки появится '
          'только после серверного триггера.';
    }
    if (!paperOnly) {
      return 'Перед отправкой сверяются цена, R:R, отпечаток показанного '
          'плана и лимиты риска. Дальше — отпечаток или ПИН устройства.';
    }
    return 'После явного подтверждения сервер создаст paper-позицию и будет '
        'вести её, даже когда приложение закрыто.';
  }
}

/// Событийный риск идеи и календарь по инструменту (прототип: `event-scenario`).
class _EventRiskCard extends StatelessWidget {
  const _EventRiskCard(
      {required this.signal, required this.idea, required this.now});

  final TradingSignal signal;
  final Idea? idea;
  final DateTime now;

  @override
  Widget build(BuildContext context) {
    final risk = idea?.eventRisk;
    final events = signal.events;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Внешние события')),
              if (risk != null)
                OutlineBadge(
                  label: risk.blocksEntry ? 'вход закрыт' : 'event risk',
                  color: risk.blocksEntry ? C.red : C.warning,
                  borderColor: (risk.blocksEntry ? C.red : C.warning)
                      .withValues(alpha: 0.35),
                  background: (risk.blocksEntry ? C.red : C.warning)
                      .withValues(alpha: 0.12),
                  fontWeight: 700,
                  radius: R.pill,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                ),
            ],
          ),
          if (risk != null) ...[
            const SizedBox(height: 9),
            InsetBox(
              padding: const EdgeInsets.fromLTRB(11, 9, 11, 10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(risk.title, style: T.body(12, weight: 700)),
                  const SizedBox(height: 3),
                  Text(
                    '${risk.until(now) == null ? 'время не предоставлено источником' : _when(risk.until(now)!)} · ${risk.policy}',
                    style: T.body(11, color: C.muted, height: 1.4),
                  ),
                ],
              ),
            ),
          ],
          if (events.isEmpty && risk == null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'Событий по инструменту в окне идеи не найдено. Это результат '
                'проверки календаря, а не отсутствие данных.',
                style: T.body(11.5, color: C.muted, height: 1.45),
              ),
            ),
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
                  child:
                      Text(event.time, style: T.mono(11, color: C.muted)),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child:
                      Text(event.text, style: T.body(12, color: C.textSoft)),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  static String _when(Duration until) => until.isNegative
      ? 'уже прошло'
      : 'через ${formatDuration(until)}';
}

/// Обоснование без разбора движка: показываем то, что записал расчёт.
class _PlainThesis extends StatelessWidget {
  const _PlainThesis({required this.note});

  final String note;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Почему идея появилась'),
            const SizedBox(height: 8),
            Text(
              note.isEmpty ? 'Обоснование не пришло вместе с идеей.' : note,
              style: T.body(12.5, color: C.textSecondary, height: 1.5),
            ),
          ],
        ),
      );
}

/// Условия отмены у сигнала без плана движка: есть только стоп и цена
/// инвалидации, и придумывать остальное нечем.
class _PlainInvalidation extends StatelessWidget {
  const _PlainInvalidation({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Когда идея отменяется'),
            const SizedBox(height: 8),
            Text(
              signal.invalidationPrice == null
                  ? 'Жёсткие условия отмены не заданы: расчёт дал только стоп. '
                      'Выход по стопу — единственное правило этой идеи.'
                  : 'Замысел считается сломанным при уходе цены за '
                      '${fmtPrice(signal.invalidationPrice!, signal.priceDecimals)}. '
                      'До этого уровня идея ведётся по стопу.',
              style: T.body(11.5, color: C.muted, height: 1.45),
            ),
          ],
        ),
      );
}

/// Шесть блоков оценки старого скринера — для идей без разбора движка.
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
            const SizedBox(height: 11),
            TileGrid(
              minTileWidth: 132,
              tiles: [
                for (final factor in signal.factors)
                  _FactorTile(factor: factor),
              ],
            ),
          ],
        ),
      );
}

class _FactorTile extends StatelessWidget {
  const _FactorTile({required this.factor});

  final SignalFactor factor;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(10, 9, 10, 10),
        decoration: BoxDecoration(
          color: C.inset,
          border: Border.all(color: C.divider),
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              factor.name,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: T.body(11, weight: 700, height: 1.3),
            ),
            const SizedBox(height: 6),
            StrengthBar(strength: factor.weight),
            const SizedBox(height: 6),
            Text(factor.text,
                style: T.body(10, color: C.muted, height: 1.35)),
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
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('На бумаге'),
          const SizedBox(height: 6),
          Text(
            note == null
                ? 'Идея в журнале не ведётся. Бумажная сделка проживается по '
                    'реальным свечам — без ключей, биржи и подтверждения: '
                    'именно она набирает выборку для допуска к живым деньгам. '
                    'Если подключён токен песочницы Т-Инвестиций, та же '
                    'заявка уходит и туда — чтобы заодно проверить, как '
                    'брокер её принимает.'
                : 'Ведётся в журнале: $note. Результат считается по реальным '
                    'свечам, уровни задним числом не правятся.',
            style: T.body(11.5,
                color: note == null ? C.muted : C.green, height: 1.5),
          ),
          if (note == null) ...[
            const SizedBox(height: 10),
            ActionButton(
              label: 'Вести на бумаге',
              onTap: controller.trackCurrentSignalOnPaper,
              dense: true,
            ),
          ],
        ],
      ),
    );
  }
}

/// Метки, которые подсвечивает выбранное доказательство (§9.1).
///
/// Связь берётся со стороны разметки: каждая метка знает своё доказательство
/// (`evidenceId`), и обратный список в доказательстве держать незачем — две
/// копии одной связи расходятся, и подсветка начинает показывать не то.
///
/// Сравнение идёт по **полному** идентификатору. Раньше здесь отрезался
/// префикс идеи и сравнивались хвосты строк: совпадение хвостов — не то же
/// самое, что совпадение объектов, и подсветить могло чужую метку.
Set<String> _highlightKeys(Idea? idea, String? evidenceId) {
  if (idea == null || evidenceId == null) return const {};
  return {
    for (final a in idea.annotations)
      if (a.evidenceId == evidenceId) a.id,
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

/// Полоса «идея закончилась» с причиной из журнала движка.
///
/// Стоит до графика намеренно. Владелец открывает разбор, видит цену у
/// третьей цели и первым делом спрашивает: это ещё сделка или уже история.
/// Ответ обязан встретить его сразу, а не обнаружиться после прокрутки —
/// и обязан быть причиной, а не одним словом состояния: «рынок ушёл без
/// нас» и «срок истёк» требуют разного.
class _ClosedNote extends StatelessWidget {
  const _ClosedNote({required this.state, required this.reason});

  final IdeaState state;
  final String reason;

  @override
  Widget build(BuildContext context) {
    final color = StateBadge.colorOf(state);
    return Container(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 11),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        border: Border.all(color: color.withValues(alpha: 0.35)),
        borderRadius: BorderRadius.circular(R.card),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            state.meaning,
            style: T.body(12, weight: 700, color: color, height: 1.35),
          ),
          const SizedBox(height: 4),
          Text(reason,
              style: T.body(11.5, color: C.textSecondary, height: 1.45)),
        ],
      ),
    );
  }
}
