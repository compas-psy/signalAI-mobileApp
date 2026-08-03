import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../data/api/engine_contract.dart';
import '../../domain/enums.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_state.dart';
import '../../domain/ledger/signal_ledger.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/confluence_ring.dart';
import '../widgets/idea_head.dart';
import '../widgets/sparkline.dart';

/// Лента идей (ТЗ §8).
///
/// Карточка отвечает на пять вопросов и молчит обо всём остальном: что за
/// инструмент и куда, в каком состоянии, какой сетап и на каких таймфреймах,
/// сколько это стоит и сколько ещё живёт. Всё прочее — в разборе идеи.
class IdeasScreen extends StatelessWidget {
  const IdeasScreen({super.key, required this.pill});

  /// Разрез ленты из пилюли раздела.
  final int pill;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final now = DateTime.now();
    final filter = IdeasPill.values[pill.clamp(0, IdeasPill.values.length - 1)];

    if (controller.digest == null) {
      return _Pending(
        loading: controller.digestLoading,
        stage: controller.analysisStage,
        error:
            controller.digestError == null ? null : controller.digestErrorText,
        onRetry: controller.refreshDigest,
      );
    }

    final all = controller.ideas;
    final visible = filterIdeas(all, filter, now);
    // «В работе» — это вопрос «что у меня сейчас открыто», а не «какие идеи
    // движок пометил Active». Бумажная позиция живёт в журнале сделок, и
    // экран писал «открытых позиций нет», пока в журнале висела открытая
    // позиция по тому же инструменту.
    final paper = filter == IdeasPill.active
        ? controller.openPaperTrades
        : const <PaperTrade>[];

    if (visible.isEmpty && paper.isEmpty) {
      return _Empty(
        filter: filter,
        total: all.length,
        unavailableReason: controller.ideasUnavailableReason,
        noSetupsReason: controller.noSetupsReason,
      );
    }

    final rows = <Widget>[
      for (final idea in visible)
        IdeaCard(idea: idea, now: now, onTap: () => controller.openSignal(idea.id)),
      if (paper.isNotEmpty) ...[
        const _SectionLabel('Бумажные позиции'),
        for (final trade in paper)
          () {
            // Идея за сделкой есть не всегда: журнал ведёт и скринер на
            // устройстве, у которого свои сигналы.
            //
            // Условие ровно одно — нашлась ли идея в выдаче. Раньше их было
            // два: надпись смотрела на выдачу, а нажатие — на то, записан ли
            // у сделки идентификатор. Сделка с идентификатором, но без идеи
            // в выдаче открывала пустой разбор под подписью «идеи за ней
            // нет». Ссылка ведёт туда, где что-то есть, или её нет вовсе.
            final source = all.where((i) => i.id == trade.signalId).firstOrNull;
            // Идея могла уйти из выдачи терминальной, пока сделка жива, —
            // разбор при этом существует на сервере и догружается по
            // идентификатору. Мёртвая ссылка остаётся только у сделок
            // скринера без идентификатора вовсе.
            final ideaId = trade.signalId;
            return PaperPositionCard(
              trade: trade,
              idea: source,
              onOpenIdea:
                  ideaId.isEmpty ? null : () => controller.openSignal(ideaId),
            );
          }(),
      ],
    ];

    return ListView.builder(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 18),
      itemCount: rows.length,
      itemBuilder: (context, i) => Padding(
        padding: EdgeInsets.only(bottom: i == rows.length - 1 ? 0 : S.gap),
        child: rows[i],
      ),
    );
  }

  /// Разрез по состоянию (ТЗ §8.2). Порядок внутри разреза — приоритет §6.2.
  static List<Idea> filterIdeas(
      List<Idea> ideas, IdeasPill filter, DateTime now) {
    final ranked = IdeaPriority.rank(ideas, now);
    return switch (filter) {
      IdeasPill.decisions =>
        ranked.where((i) => i.state.needsAttention).toList(),
      IdeasPill.watch =>
        ranked.where((i) => i.state == IdeaState.watch).toList(),
      IdeasPill.active =>
        ranked.where((i) => i.state == IdeaState.active).toList(),
      IdeasPill.all => ranked,
    };
  }
}

/// Карточка идеи в списке (ТЗ §8.1).
class IdeaCard extends StatelessWidget {
  const IdeaCard({
    super.key,
    required this.idea,
    required this.now,
    this.onTap,
  });

  final Idea idea;
  final DateTime now;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final plan = idea.plan;
    final controller = AppScope.of(context);
    // Мини-график цены — то, с чего трейдер начинает читать идею. Свечи
    // тянутся лениво и кэшируются; пока их нет, карточка живёт без графика,
    // а не ждёт его.
    final chart = controller.ideaChart(idea.id);
    if (chart == null) controller.loadIdeaChart(idea);
    return Pressable(
      onTap: onTap,
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              // Тикер, а не канонический идентификатор.
                              // «CRYPTO:PERP:BTCUSDT» в заголовок не влезает и
                              // обрезается на «CRYPTO:PER…» — по такой подписи
                              // нельзя понять, о каком активе идея.
                              EngineContract.symbolOf(idea.instrumentId),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: T.jost(17),
                            ),
                          ),
                          const SizedBox(width: 7),
                          DirectionBadge(
                            label: idea.direction.label,
                            color: directionColor(idea.direction),
                            background: directionBackground(idea.direction),
                          ),
                          const SizedBox(width: 6),
                          StateBadge(state: idea.state),
                        ],
                      ),
                      const SizedBox(height: 5),
                      // Имя инструмента: «BTCUSDT» знает не всякий, кто помнит,
                      // что торгует биткоин к доллару бессрочным контрактом.
                      if (idea.instrumentName.isNotEmpty &&
                          idea.instrumentName !=
                              EngineContract.symbolOf(idea.instrumentId)) ...[
                        Text(
                          idea.instrumentName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: T.body(11.5, color: C.textSecondary),
                        ),
                        const SizedBox(height: 3),
                      ],
                      Text(
                        // Пустой список таймфреймов оставлял висящую точку
                        // после названия стратегии: «Wyckoff Reversal ·».
                        [
                          idea.strategy.label,
                          if (idea.timeframes.isNotEmpty)
                            idea.timeframes.join(' / '),
                        ].join(' · '),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(11.5, color: C.muted),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                ConfluenceRing(score: idea.score.value),
              ],
            ),
            if (chart != null && chart.candles.length >= 2) ...[
              const SizedBox(height: 11),
              Sparkline(
                values: [for (final c in chart.candles) c.close],
                height: 44,
                color: directionColor(idea.direction),
              ),
            ],
            const SizedBox(height: 11),
            // Чипы — только то, что несёт число или срок. «Плана нет» и
            // «риск не выделен» текстом на каждой карточке — это шум:
            // состояние Watch уже сказано бейджем в заголовке.
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                TagChip(idea.market.label),
                if (plan != null) TagChip('R/R ${_rr(plan.rrToSecondTarget)}'),
                if (plan != null && plan.riskRubles > 0)
                  TagChip(riskLabel(idea)),
                TagChip(ttlLabel(idea, now)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  static String _rr(double value) =>
      value.toStringAsFixed(1).replaceAll('.', ',');

  /// Риск сделки. Идея без права входа честно говорит «риск не выделен»,
  /// а не показывает ноль — ноль читается как «бесплатно».
  static String riskLabel(Idea idea) {
    final plan = idea.plan;
    if (plan == null || plan.riskRubles <= 0) return 'риск не выделен';
    return 'риск ${money(plan.riskRubles)} ₽ · '
        '${plan.riskPercent.toStringAsFixed(2).replaceAll('.', ',')}%';
  }

  static String ttlLabel(Idea idea, DateTime now) {
    if (idea.state.isTerminal) return idea.state.label;
    final left = idea.remaining(now);
    if (left.isNegative) return 'срок вышел';
    if (left.inHours >= 24) return 'ещё ${left.inDays} дн';
    if (left.inHours >= 1) {
      return 'ещё ${left.inHours} ч ${left.inMinutes % 60} мин';
    }
    return 'ещё ${left.inMinutes} мин';
  }

  static String money(double v) => v
      .round()
      .toString()
      .replaceAllMapped(RegExp(r'\B(?=(\d{3})+(?!\d))'), (_) => ' ');
}

class _Empty extends StatelessWidget {
  const _Empty({
    required this.filter,
    required this.total,
    this.unavailableReason,
    this.noSetupsReason,
  });

  final IdeasPill filter;
  final int total;

  /// Почему движок не ответил. Это **не** «сетапов нет» (§24): обрыв связи и
  /// спокойный рынок выглядят на экране одинаково, если разницу не назвать,
  /// и владелец спокойно ждёт сигналов от сервера, который лежит.
  final String? unavailableReason;

  /// Почему движок ответил пустым списком.
  final String? noSetupsReason;

  @override
  Widget build(BuildContext context) {
    if (unavailableReason != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text('Движок не ответил', style: T.jost(18)),
              const SizedBox(height: 8),
              Text(
                unavailableReason!,
                textAlign: TextAlign.center,
                style: T.body(12, color: C.warning, height: 1.5),
              ),
              const SizedBox(height: 8),
              Text(
                'Это не «сегодня нет сетапов»: идеи считает сервер, и пока он '
                'молчит, сказать про рынок нечего.',
                textAlign: TextAlign.center,
                style: T.body(12, color: C.muted, height: 1.5),
              ),
            ],
          ),
        ),
      );
    }

    // Честный пустой экран (ТЗ §6): называет, чего именно нет и почему.
    final (title, note) = switch (filter) {
      IdeasPill.decisions => (
          'Решений нет',
          total == 0
              ? 'Расчёт не нашёл ни одной идеи выше порога показа. Это не '
                  'сбой: в узком рынке сетапов может не быть неделями.'
              : 'Ни одна идея не дошла до состояния, требующего решения. '
                  'Остальные — на вкладках «Наблюдение» и «Все».'
        ),
      IdeasPill.watch => (
          'Наблюдать нечего',
          'Идей с контекстом, но без триггера, сейчас нет.'
        ),
      IdeasPill.active => (
          'Открытых позиций нет',
          'Здесь будут подтверждённые идеи движка и открытые бумажные '
              'сделки из журнала — вместе, потому что вопрос один: что '
              'сейчас в работе.'
        ),
      IdeasPill.all => (
          'Идей нет',
          noSetupsReason ??
              'Движок не нашёл ни одной идеи выше порога показа 65.'
        ),
    };
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(title, style: T.jost(18)),
            const SizedBox(height: 8),
            Text(
              note,
              textAlign: TextAlign.center,
              style: T.body(12, color: C.muted, height: 1.5),
            ),
          ],
        ),
      ),
    );
  }
}

class _Pending extends StatelessWidget {
  const _Pending({
    required this.loading,
    required this.stage,
    required this.error,
    required this.onRetry,
  });

  final bool loading;
  final String? stage;
  final String? error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (loading || error == null) ...[
                BusyLine(label: stage ?? 'Подключаемся к биржам…'),
                const SizedBox(height: 12),
                Text(
                  'Идёт расчёт по котировкам MOEX ISS и Bybit. Остальные '
                  'разделы уже работают — расчёт не прервётся.',
                  textAlign: TextAlign.center,
                  style: T.body(11.5, color: C.muted, height: 1.5),
                ),
              ] else ...[
                Text('Данные бирж недоступны', style: T.jost(18)),
                const SizedBox(height: 8),
                Text(
                  error!,
                  textAlign: TextAlign.center,
                  style: T.body(12, color: C.muted, height: 1.5),
                ),
                const SizedBox(height: 16),
                ActionButton(label: 'Повторить', onTap: onRetry, primary: true),
              ],
            ],
          ),
        ),
      );
}

/// Подпись группы внутри ленты.
class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 6, bottom: 2),
        child: SectionLabel(text, color: C.faint),
      );
}

/// Открытая бумажная позиция в ленте «В работе».
///
/// Существует потому, что «что у меня открыто» и «какие идеи движок пометил
/// Active» — разные вопросы, а экран отвечал только на второй. Журнал при
/// этом показывал открытую позицию, и два раздела приложения противоречили
/// друг другу на одном и том же инструменте.
///
/// Карточка не притворяется идеей: у бумажной сделки свой вход, свой
/// плавающий результат и своё происхождение, и всё это названо.
class PaperPositionCard extends StatelessWidget {
  const PaperPositionCard({
    super.key,
    required this.trade,
    this.idea,
    this.onOpenIdea,
  });

  final PaperTrade trade;

  /// Идея движка, из которой сделка родилась. null — её нет: журнал ведёт и
  /// скринер на устройстве, у которого сигналы свои.
  final Idea? idea;

  final VoidCallback? onOpenIdea;

  /// Откуда сделка и можно ли из неё куда-то перейти.
  String _origin() {
    final source = idea;
    if (source != null) return 'из идеи ${source.strategy.label}';
    if (trade.signalId.isEmpty) {
      return 'заведена расчётом на устройстве — идеи за ней не записано';
    }
    return 'идеи за ней нет в текущей выдаче — открывать нечего';
  }

  @override
  Widget build(BuildContext context) {
    final waiting = trade.status == PaperStatus.pending;
    final r = trade.unrealizedR;
    final decimals = trade.entry >= 1000 ? 2 : 4;
    return Pressable(
      onTap: onOpenIdea,
      child: SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    trade.symbol,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: T.jost(17, letterSpacing: -0.3),
                  ),
                ),
                const SizedBox(width: 8),
                DirectionBadge(
                  label: trade.long ? 'LONG' : 'SHORT',
                  color: directionColor(
                    trade.long ? Direction.long : Direction.short,
                  ),
                  background: directionBackground(
                    trade.long ? Direction.long : Direction.short,
                  ),
                ),
                if (r != null) ...[
                  const SizedBox(width: 8),
                  Text(
                    '${r >= 0 ? '+' : '−'}${r.abs().toStringAsFixed(2).replaceAll('.', ',')}R',
                    style: T.mono(13, weight: 600, color: r >= 0 ? C.green : C.red),
                  ),
                ],
              ],
            ),
            const SizedBox(height: 6),
            Text(
              waiting
                  ? 'заявка выставлена по ${fmtPrice(trade.entry, decimals)} — '
                      'цена до неё не дошла'
                  : 'в позиции с ${fmtPrice(trade.entry, decimals)} · '
                      'взято тейков: ${trade.tpsTaken} из ${trade.tpPrices.length}',
              style: T.body(11.5, color: C.textSecondary, height: 1.4),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                const ContextChip('бумажная'),
                // Происхождение сделки названо прямо. Без этого позиция по
                // тому же тикеру, что и идея движка, читается как «та самая
                // идея» — а вход у неё другой.
                //
                // Причин «открывать нечего» две, и они разные: у сделки может
                // не быть ссылки на идею вовсе, а может быть ссылка на идею,
                // которой больше нет в выдаче. Первое — свойство сделки,
                // второе — свойство ленты, и владелец вправе их различать.
                ContextChip(_origin()),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
