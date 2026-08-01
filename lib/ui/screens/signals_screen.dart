import 'package:flutter/widgets.dart';

import '../../domain/research/hypothesis.dart';
import '../../state/app_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Ранние сигналы: гипотезы об экономике эмитента (ТЗ Early Signals).
///
/// Раздел живёт в «Портфеле», а не в «Идеях», и это решение, а не раскладка.
/// Идея отвечает, когда входить: у неё есть зона входа, стоп, цели и срок в
/// днях, и она исполняется по биометрии. Гипотеза отвечает, что изменилось
/// в экономике компании и за какой срок это должно проявиться в отчётности.
/// Горизонт — год-два, исполнять её нечем, и кнопки подтверждения здесь нет
/// и не будет: ТЗ прямо запрещает выдавать BUY, SELL и размер позиции.
///
/// Соседство с пакетами не случайно: гипотеза отвечает на вопрос «что
/// вообще стоит держать», то есть на тот же вопрос, что и состав пакета, —
/// только раньше и по другим данным.
///
/// Экран обязан объяснять emptyту. Контур зависит от источников, у которых
/// разный правовой режим, и «гипотез нет, потому что рынок спокоен»
/// неотличимо от «гипотез нет, потому что ни один источник не подключён» —
/// а это разные новости, и вторая требует действия владельца.
class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key, required this.controller});

  final AppController controller;

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  @override
  void initState() {
    super.initState();
    widget.controller.loadResearch();
  }

  @override
  Widget build(BuildContext context) {
    final controller = widget.controller;
    final state = controller.research;

    if (state == null) {
      return _Note(
        title: 'Ранние сигналы',
        text: controller.researchLoading
            ? 'Спрашиваю движок…'
            : 'Гипотезы считает сервер — запрос ещё не ушёл.',
        busy: controller.researchLoading,
      );
    }
    if (!state.isAvailable) {
      return _Note(
        title: 'Движок не ответил',
        text: state.unavailableReason!,
        onRetry: () => controller.loadResearch(force: true),
      );
    }

    // Порядок здесь и есть ответ на вопрос «что у меня по сигналам».
    // Гипотезы сверху, устройство контура — под раскрытием: пока их нет,
    // объяснение нужно, а когда они есть, оно отодвигает результат вниз и
    // заставляет искать его прокруткой.
    final empty = state.hypotheses.isEmpty;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        if (empty)
          _Waiting(state: state)
        else
          for (final h in state.hypotheses) ...[
            _HypothesisCard(hypothesis: h),
            const SizedBox(height: 10),
          ],
        const SizedBox(height: 12),
        _Details(state: state),
      ],
    );
  }
}

/// Чем это отличается от идей. Ставится первым и не сворачивается.
///
/// Без этой карточки раздел читается как «идеи, только медленнее», и первое
/// же движение владельца будет попыткой по нему торговать.
class _Explainer extends StatelessWidget {
  const _Explainer();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Что это'),
            const SizedBox(height: 6),
            Text(
              'Гипотеза — не сделка. Идея говорит, когда входить: у неё есть '
              'зона входа, стоп и срок в днях. Гипотеза говорит, что '
              'изменилось в экономике компании и за какой срок это должно '
              'проявиться в отчётности. Горизонт — год-два.',
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
            const SizedBox(height: 8),
            Text(
              'Здесь нет и не будет кнопки «купить»: максимум, который выдаёт '
              'система, — «стоит изучить вручную». Каждая гипотеза несёт то, '
              'что её опровергнет, и срок, после которого она протухает.',
              style: T.body(11.5, color: C.faint, height: 1.5),
            ),
          ],
        ),
      );
}

/// Одна гипотеза: утверждение, чем доказано, чем опровергается.
class _HypothesisCard extends StatelessWidget {
  const _HypothesisCard({required this.hypothesis});

  final Hypothesis hypothesis;

  Color get _stateColor => switch (hypothesis.state) {
        HypothesisState.diligenceReady => C.green,
        HypothesisState.confirmed => C.info,
        HypothesisState.earlyCandidate => C.accent,
        HypothesisState.invalidated || HypothesisState.rejected => C.red,
        _ => C.muted,
      };

  @override
  Widget build(BuildContext context) {
    final h = hypothesis;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (h.symbol.isNotEmpty) ...[
                Text(h.symbol, style: T.jost(15, weight: 700, color: C.text)),
                const SizedBox(width: 8),
              ],
              Expanded(
                child: Text(
                  h.state.label.toUpperCase(),
                  style: T.mono(9.5, color: _stateColor),
                ),
              ),
              // Приоритет проверки, а не ожидаемая доходность. Подпись
              // обязательна: число без неё читается как прогноз.
              Text(
                h.researchPriority.toStringAsFixed(0),
                style: T.mono(13, weight: 700, color: _stateColor),
              ),
            ],
          ),
          Align(
            alignment: Alignment.centerRight,
            child: Text('приоритет проверки', style: T.body(9, color: C.faint)),
          ),
          const SizedBox(height: 8),
          Text(h.title, style: T.body(12.5, color: C.text, height: 1.4)),
          const SizedBox(height: 4),
          Text(h.state.meaning, style: T.body(10.5, color: C.faint, height: 1.4)),
          const SizedBox(height: 10),
          _Scores(hypothesis: h),
          if (h.shortfall.isNotEmpty) ...[
            const SizedBox(height: 10),
            const SectionLabel('До подтверждения не хватает', color: C.faint),
            const SizedBox(height: 4),
            for (final gap in h.shortfall)
              Padding(
                padding: const EdgeInsets.only(bottom: 2),
                child: Text('· $gap',
                    style: T.body(10.5, color: C.warning, height: 1.4)),
              ),
          ],
          if (h.targetKpis.isNotEmpty) ...[
            const SizedBox(height: 10),
            const SectionLabel('Что должно измениться', color: C.faint),
            const SizedBox(height: 4),
            for (final kpi in h.targetKpis)
              KeyValueRow(
                name: kpi.metric,
                value: kpi.window.isEmpty ? kpi.expectedDirection : kpi.window,
                valueStyle: T.mono(11, color: C.textSoft),
                showDivider: false,
              ),
          ],
          if (h.falsifiers.isNotEmpty) ...[
            const SizedBox(height: 10),
            // Утверждение, которое нечем опровергнуть, — не гипотеза. Этот
            // блок здесь не для полноты: он и есть проверяемость.
            const SectionLabel('Что её опровергнет', color: C.faint),
            const SizedBox(height: 4),
            for (final f in h.falsifiers)
              Padding(
                padding: const EdgeInsets.only(bottom: 2),
                child: Text('· $f',
                    style: T.body(10.5, color: C.textSecondary, height: 1.4)),
              ),
          ],
          if (h.evidence.any((e) => e.contradicts)) ...[
            const SizedBox(height: 10),
            const SectionLabel('Противоречит', color: C.faint),
            const SizedBox(height: 4),
            // Опровергающее доказательство не прячется никогда: гипотеза,
            // из которой вычистили неудобные факты, перестаёт быть
            // проверяемой.
            for (final e in h.evidence.where((e) => e.contradicts))
              Padding(
                padding: const EdgeInsets.only(bottom: 2),
                child: Text('· ${e.claim}',
                    style: T.body(10.5, color: C.red, height: 1.4)),
              ),
          ],
          if (h.expectedLag.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(
              'ожидаемый лаг ${h.expectedLag}',
              style: T.mono(9.5, color: C.faint),
            ),
          ],
        ],
      ),
    );
  }
}

/// Три оценки раздельно — сводить их в одну запрещено ТЗ §2.6.
class _Scores extends StatelessWidget {
  const _Scores({required this.hypothesis});

  final Hypothesis hypothesis;

  @override
  Widget build(BuildContext context) => Row(
        children: [
          _Score(
            label: 'доказано',
            value: hypothesis.evidenceScore,
            hint: 'надёжность источников',
          ),
          const SizedBox(width: 8),
          _Score(
            label: 'материально',
            value: hypothesis.economicScore,
            hint: 'размер эффекта',
          ),
          const SizedBox(width: 8),
          _Score(
            label: 'в цене',
            value: hypothesis.marketContextScore,
            // null остаётся null: подставить среднее значило бы выдумать
            // знание о том, учтено ли изменение рынком.
            hint: hypothesis.marketContextScore == null
                ? 'не измерено'
                : 'учтено рынком',
          ),
        ],
      );
}

class _Score extends StatelessWidget {
  const _Score({required this.label, required this.value, required this.hint});

  final String label;
  final double? value;
  final String hint;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              value == null ? '—' : (value! * 100).toStringAsFixed(0),
              style: T.mono(
                14,
                weight: 700,
                color: value == null ? C.faint : C.textSoft,
              ),
            ),
            Text(label, style: T.body(10, color: C.muted)),
            Text(hint, style: T.body(9, color: C.faint)),
          ],
        ),
      );
}

/// Девять движков и чего каждому не хватает.
///
/// Блок отвечает на вопрос, который иначе останется без ответа: emptyй
/// экран — это «система не дописана» или «система готова, но её нечем
/// кормить»? Разница определяет, что делать дальше, и потому показывается
/// числом, а не подразумевается.
class _Engines extends StatelessWidget {
  const _Engines({required this.state});

  final ResearchState state;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Движки'),
            const SizedBox(height: 6),
            Text(
              'Написано и проверено ${state.enginesReady} из '
              '${state.engines.length}. Все входы закрыты у '
              '${state.enginesFed}, часть входов — у '
              '${state.engines.where((e) => e.partial).length}.',
              style: T.body(11.5, color: C.text, height: 1.45),
            ),
            const SizedBox(height: 10),
            for (final engine in state.engines)
              KeyValueRow(
                name: engine.title,
                value: engine.stateLabel,
                valueStyle: T.mono(
                  11,
                  color: engine.fed
                      ? C.green
                      : (engine.partial ? C.info : C.warning),
                ),
                showDivider: engine != state.engines.last,
              ),
          ],
        ),
      );
}

/// Откуда берутся данные и почему их пока нет.
///
/// Список не декоративный. Контур зависит от источников с разным правовым
/// режимом, и владельцу нужно видеть три разные вещи: что можно подключить
/// бесплатно прямо сейчас, что придётся носить руками, и что не будет
/// подключено никогда — с заменой.
class _Sources extends StatelessWidget {
  const _Sources({required this.state});

  final ResearchState state;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Откуда данные'),
            const SizedBox(height: 6),
            if (state.reason.isNotEmpty) ...[
              Text(state.reason,
                  style: T.body(11.5, color: C.text, height: 1.45)),
              const SizedBox(height: 10),
            ],
            KeyValueRow(
              name: 'Бесплатный официальный маршрут',
              value: '${state.freeRoute} из ${state.totalSources}',
              valueStyle: T.mono(12, color: C.green),
            ),
            KeyValueRow(
              name: 'Ждут проверки условий',
              value: '${state.awaitingTermsCheck.length}',
              valueStyle: T.mono(12, color: C.warning),
            ),
            KeyValueRow(
              name: 'Только ручной импорт',
              value: '${state.manualOnly.length}',
              valueStyle: T.mono(12, color: C.muted),
              showDivider: state.unavailable.isNotEmpty,
            ),
            if (state.unavailable.isNotEmpty) ...[
              const SizedBox(height: 10),
              const SectionLabel('Недоступны', color: C.faint),
              const SizedBox(height: 4),
              for (final source in state.unavailable)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        '${source.name} · ${source.statusLabel}',
                        style: T.body(11, color: C.textSecondary),
                      ),
                      Text(
                        source.note,
                        style: T.body(10, color: C.faint, height: 1.4),
                      ),
                    ],
                  ),
                ),
            ],
            if (state.connectOrder.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                'Порядок подключения: ${state.connectOrder.take(6).join(', ')}…',
                style: T.mono(9.5, color: C.dim, height: 1.4),
              ),
            ],
          ],
        ),
      );
}

class _Note extends StatelessWidget {
  const _Note({
    required this.title,
    required this.text,
    this.busy = false,
    this.onRetry,
  });

  final String title;
  final String text;
  final bool busy;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => ListView(
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
        children: [
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SectionLabel(title),
                const SizedBox(height: 6),
                Text(text, style: T.body(11.5, color: C.muted, height: 1.5)),
                if (onRetry != null) ...[
                  const SizedBox(height: 10),
                  ActionButton(label: 'Спросить ещё раз', onTap: onRetry!),
                ],
              ],
            ),
          ),
        ],
      );
}

/// Одна строка вместо трёх карточек, пока гипотез нет.
///
/// «Почему empty» — вопрос из одного предложения, и отвечать на него
/// страницей про правовые режимы источников значит топить ответ в
/// подробностях. Подробности никуда не делись: они под раскрытием ниже.
class _Waiting extends StatelessWidget {
  const _Waiting({required this.state});

  final ResearchState state;

  @override
  Widget build(BuildContext context) {
    final fed = state.engines.where((e) => e.fed || e.partial).length;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Гипотез пока нет', style: T.jost(15, weight: 700, color: C.text)),
          const SizedBox(height: 6),
          Text(
            fed == 0
                ? 'Движки ещё не получили данных.'
                : 'Считают $fed из ${state.engines.length}. '
                    'Гипотеза появится, когда сойдутся независимые источники.',
            style: T.body(12.5, color: C.muted, height: 1.4),
          ),
        ],
      ),
    );
  }
}

/// Устройство контура под раскрытием.
///
/// Владельцу нужен результат, а не отчёт о том, как он получается. Но и
/// спрятать это насовсем нельзя: когда гипотез нет, единственный способ
/// понять, что делать, — увидеть, чего не хватает движкам.
class _Details extends StatefulWidget {
  const _Details({required this.state});

  final ResearchState state;

  @override
  State<_Details> createState() => _DetailsState();
}

class _DetailsState extends State<_Details> {
  bool open = false;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          GestureDetector(
            onTap: () => setState(() => open = !open),
            behavior: HitTestBehavior.opaque,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 8),
              child: Text(
                open ? 'Скрыть устройство контура' : 'Как это считается',
                style: T.jost(13, weight: 600, color: C.accent),
              ),
            ),
          ),
          if (open) ...[
            const _Explainer(),
            const SizedBox(height: 12),
            _Engines(state: widget.state),
            const SizedBox(height: 12),
            _Sources(state: widget.state),
          ],
        ],
      );
}
