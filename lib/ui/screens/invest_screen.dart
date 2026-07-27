import 'package:flutter/widgets.dart';

import '../../domain/invest/invest_models.dart';
import '../../domain/ledger/signal_ledger.dart';
import '../../domain/enums.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../layout.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/confluence_ring.dart';
import '../widgets/level_strip.dart';
import '../widgets/segmented.dart';
import '../widgets/trade_chart.dart';

/// Раздел «Инвест»: среднесрочные идеи по акциям РФ (1–3 месяца, лонги).
///
/// Правила честности написаны в самом экране: техника проверена бэктестом на
/// годах дневной истории; фундаментальный паспорт — текущий срез из Invest
/// API, в ранжире не участвует и бэктестом не проверяется; исполнение —
/// руками, форвард-статистика ведётся отдельным бумажным журналом.
class InvestScreen extends StatelessWidget {
  const InvestScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final digest = controller.invest;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: digest == null
              ? _Pending(controller: controller)
              : ListView(
                  padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
                  children: [
                    // Идеи идут во всю ширину: у карточки внутри график и
                    // паспорт, в половине колонки они бы схлопнулись.
                    _StatusCard(digest: digest),
                    const SizedBox(height: 12),
                    if (digest.ideas.isEmpty) ...[
                      _EmptyCard(digest: digest),
                      const SizedBox(height: 12),
                    ],
                    for (final idea in digest.ideas) ...[
                      _IdeaCard(idea: idea),
                      const SizedBox(height: 12),
                    ],
                    CardGrid(
                      children: [
                        if (digest.watchlist.isNotEmpty)
                          _WatchlistCard(watchlist: digest.watchlist),
                        _JournalCard(ledger: controller.investDesk?.investLedger),
                        _BacktestCard(controller: controller),
                        if (digest.rejections.isNotEmpty)
                          _RejectionsCard(rejections: digest.rejections),
                      ],
                    ),
                  ],
                ),
        ),
      ],
    );
  }
}

class _Pending extends StatelessWidget {
  const _Pending({required this.controller});

  final dynamic controller;

  @override
  Widget build(BuildContext context) {
    final loading = controller.investLoading as bool;
    final errorText = controller.investErrorText as String;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(28),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(loading ? 'Сканируем рынок акций…' : 'Раздел ещё не считался',
                style: T.jost(20)),
            const SizedBox(height: 10),
            if (loading)
              Text(
                (controller.analysisStage as String?) ?? 'Доска акций TQBR…',
                textAlign: TextAlign.center,
                style: T.mono(12, color: C.accent),
              )
            else ...[
              if (errorText.isNotEmpty)
                Text(errorText,
                    textAlign: TextAlign.center,
                    style: T.body(12, color: C.muted, height: 1.5)),
              const SizedBox(height: 16),
              Pressable(
                onTap: () => controller.refreshInvest(force: true),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 22, vertical: 13),
                  decoration: BoxDecoration(
                    color: C.accent,
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Text('Сканировать сейчас',
                      style: T.body(14, weight: 800, color: C.onAccent)),
                ),
              ),
            ],
            if (loading) ...[
              const SizedBox(height: 10),
              Text(
                'Первый прогон долгий: качается дневная история всей доски '
                'TQBR за три года. Дальше — инкрементально и ночью.',
                textAlign: TextAlign.center,
                style: T.body(11, color: C.muted, height: 1.5),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({required this.digest});

  final InvestDigest digest;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final at = digest.at;
    final when =
        '${at.day.toString().padLeft(2, '0')}.${at.month.toString().padLeft(2, '0')} '
        '${at.hour.toString().padLeft(2, '0')}:${at.minute.toString().padLeft(2, '0')}';
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (digest.regimeNote.isNotEmpty) ...[
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  margin: const EdgeInsets.only(top: 5),
                  width: 7,
                  height: 7,
                  decoration: BoxDecoration(
                    color: digest.regimeBlocksLongs ? C.red : C.green,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Text(
                    digest.regimeNote,
                    style: T.body(12,
                        weight: 700,
                        color: digest.regimeBlocksLongs ? C.red : C.green,
                        height: 1.4),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),
          ],
          // Воронка отбора одной моношириной строкой: видно, где рынок
          // кончился, без чтения абзаца.
          InsetBox(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
            child: Text(
              'TQBR ${digest.universeSize} → ликвидных ${digest.liquidSize} → '
              'с историей ${digest.tradableSize} → идей ${digest.ideas.length}',
              style: T.mono(11.5, weight: 600, color: C.textSecondary, height: 1.5),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Пересчитано $when МСК · ночью раз в день. Техника проверяется '
            'бэктестом; фундаментальный паспорт — текущий срез Invest API, в '
            'отборе идей не участвует. Исполнение — руками.',
            style: T.body(11, color: C.muted, height: 1.5),
          ),
          if (digest.passportNote.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text('Паспорта: ${digest.passportNote}',
                style: T.body(11, color: C.accent, height: 1.4)),
          ],
          const SizedBox(height: 10),
          Pressable(
            onTap: () => controller.refreshInvest(force: true),
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 10),
              decoration: BoxDecoration(
                border: Border.all(color: C.borderHover),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: Center(
                child: Text('Пересчитать сейчас',
                    style: T.body(12, weight: 800, color: C.accent)),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _IdeaCard extends StatelessWidget {
  const _IdeaCard({required this.idea});

  final InvestIdea idea;

  @override
  Widget build(BuildContext context) {
    final signal = idea.signal;
    final decimals = signal.priceDecimals;
    String price(double v) => v.toStringAsFixed(decimals).replaceAll('.', ',');
    // Апсайд до дальней цели — то, ради чего держат позицию месяцами.
    final target = signal.takeProfits.isEmpty ? null : signal.takeProfits.last.price;
    final upside = target == null || signal.entry <= 0
        ? null
        : (target - signal.entry) / signal.entry * 100;

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ConfluenceRing(score: signal.score),
              const SizedBox(width: 11),
              Expanded(
                child: Row(
                  children: [
                    Flexible(
                      child: Text(
                        signal.symbol,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.jost(17),
                      ),
                    ),
                    const SizedBox(width: 7),
                    DirectionBadge(
                      label: signal.direction.label,
                      color: directionColor(signal.direction),
                      background: directionBackground(signal.direction),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(signal.lastPrice, style: T.mono(13.5, weight: 600)),
                  Text(signal.changeLabel,
                      style: T.mono(11, color: signal.changeUp ? C.green : C.red)),
                ],
              ),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            '${signal.name} · до 60 торговых дней',
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: T.body(11.5, color: C.muted),
          ),
          const SizedBox(height: 10),
          if (signal.chart != null) ...[
            ClipRRect(
              borderRadius: BorderRadius.circular(R.inner),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  border: Border.all(color: C.divider),
                  borderRadius: BorderRadius.circular(R.inner),
                ),
                child: TradeChart(signal: signal),
              ),
            ),
            const SizedBox(height: 10),
          ],
          LevelStrip(
            entry: price(signal.entry),
            stop: price(signal.stopLoss),
            targets: [for (final tp in signal.takeProfits) price(tp.price)],
            riskReward: '${signal.riskReward} R:R',
          ),
          if (upside != null) ...[
            const SizedBox(height: 8),
            OutlineBadge(
              label: 'потенциал ${upside >= 0 ? '+' : '−'}'
                  '${upside.abs().toStringAsFixed(0)}% до дальней цели',
              color: C.green,
              borderColor: C.greenBorder,
              background: C.greenFaint,
              fontWeight: 700,
              padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
            ),
          ],
          const SizedBox(height: 12),
          Text(_lead(signal.note),
              style: T.body(12, color: C.textSecondary, height: 1.5)),
          const SizedBox(height: 10),
          for (final factor in signal.factors)
            Padding(
              padding: const EdgeInsets.only(top: 6),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  SizedBox(
                    width: 108,
                    child: Text(factor.name,
                        style: T.body(11, weight: 700, height: 1.4)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(factor.text,
                        style: T.body(11, color: C.muted, height: 1.4)),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 12),
          _Passport(passport: idea.passport),
        ],
      ),
    );
  }
}

/// Первое предложение обоснования: вывод крупно, подробности — ниже строками.
String _lead(String note) {
  final end = note.indexOf('. ');
  return end < 0 ? note : note.substring(0, end + 1);
}

/// Фундаментальный паспорт: восемь показателей плитками 4×2.
///
/// Плитки, а не строки «ключ — значение»: восемь чисел в столбик читаются как
/// текст, а сеткой — как приборная панель. «Хорошие» значения подсвечены
/// зелёным, но подсветка ни на что не влияет: паспорт в ранжире не участвует
/// и бэктестом не проверяется, о чём написано прямо в карточке.
class _Passport extends StatelessWidget {
  const _Passport({required this.passport});

  final FundamentalsPassport? passport;

  @override
  Widget build(BuildContext context) {
    final p = passport;
    if (p == null) {
      return Text(
        'Фундаментальный паспорт недоступен — идея построена только на '
        'технике (это её обычный режим: паспорт в отборе не участвует).',
        style: T.body(10.5, color: C.muted, height: 1.4),
      );
    }
    String num(double? v, {int digits = 1, String suffix = ''}) => v == null
        ? '—'
        : '${v.toStringAsFixed(digits).replaceAll('.', ',')}$suffix';
    String pct(double? v) => num(v, suffix: '%');

    final reportDate = p.nextReportDate;
    final daysToReport = reportDate?.difference(DateTime.now()).inDays;

    // Пороги «хорошего» — общепринятые ориентиры российского рынка, не наши
    // выдумки: дешёвая оценка, здоровый долг, ощутимый дивиденд.
    Color good(double? v, bool Function(double) test) =>
        v != null && test(v) ? C.green : C.text;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(child: Text('Фундаментальный паспорт', style: T.body(12, weight: 800))),
            Text('срез, не ранжир', style: T.body(9.5, color: C.muted)),
          ],
        ),
        const SizedBox(height: 8),
        MetricRow(tiles: [
          MetricTile(label: 'P/E', value: num(p.peTtm), color: good(p.peTtm, (v) => v > 0 && v < 8)),
          MetricTile(
              label: 'EV/EBITDA',
              value: num(p.evToEbitda),
              color: good(p.evToEbitda, (v) => v > 0 && v < 5)),
          MetricTile(label: 'ROE', value: pct(p.roe), color: good(p.roe, (v) => v >= 15)),
          MetricTile(
              label: 'Долг/EBITDA',
              value: num(p.debtToEbitda),
              color: good(p.debtToEbitda, (v) => v < 2)),
        ]),
        const SizedBox(height: 8),
        MetricRow(tiles: [
          MetricTile(
              label: 'Див TTM',
              value: pct(p.dividendYieldTtm),
              color: good(p.dividendYieldTtm, (v) => v >= 8)),
          MetricTile(
              label: 'Див вперёд',
              value: pct(p.forwardDividendYield),
              color: good(p.forwardDividendYield, (v) => v >= 8)),
          MetricTile(
              label: 'Выручка г/г',
              value: pct(p.revenueGrowthOneYear),
              color: good(p.revenueGrowthOneYear, (v) => v >= 10)),
          MetricTile(
              label: 'Маржа',
              value: pct(p.netMargin),
              color: good(p.netMargin, (v) => v >= 15)),
        ]),
        if (p.consensus != null || p.targetPrice != null) ...[
          const SizedBox(height: 8),
          _PassportLine(
            color: C.muted,
            text: 'Аналитики: ${_consensusLabel(p.consensus)} · цель '
                '${num(p.targetPrice, digits: 2)}'
                '${p.buyCount == null ? '' : ' · покупать/держать/продавать '
                    '${p.buyCount}/${p.holdCount ?? 0}/${p.sellCount ?? 0}'}',
          ),
        ],
        if (daysToReport != null && daysToReport <= 14)
          _PassportLine(
            color: C.accent,
            text: 'Отчётность через $daysToReport дн. '
                '(${reportDate!.day.toString().padLeft(2, '0')}.'
                '${reportDate.month.toString().padLeft(2, '0')}) — '
                'гэп на публикации вероятен, учитывайте при входе.',
          ),
        Padding(
          padding: const EdgeInsets.only(top: 8),
          child: Text(
            'Паспорт — текущий срез Invest API от '
            '${p.at.day.toString().padLeft(2, '0')}.${p.at.month.toString().padLeft(2, '0')}: '
            'истории показателей API не отдаёт, поэтому проверить их вклад '
            'бэктестом нельзя — и мы не делаем вид, что можно.',
            style: T.body(9.5, color: C.muted, height: 1.4),
          ),
        ),
      ],
    );
  }

  static String _consensusLabel(String? v) => switch (v) {
        'BUY' || 'RECOMMENDATION_BUY' => 'покупать',
        'SELL' || 'RECOMMENDATION_SELL' => 'продавать',
        'HOLD' || 'RECOMMENDATION_HOLD' => 'держать',
        null => '—',
        _ => v,
      };
}

/// Строка под паспортом с цветной точкой важности слева.
class _PassportLine extends StatelessWidget {
  const _PassportLine({required this.text, required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(top: 7),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Container(
              margin: const EdgeInsets.only(top: 5),
              width: 6,
              height: 6,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(text, style: T.body(11, color: C.muted, height: 1.4)),
            ),
          ],
        ),
      );
}

class _JournalCard extends StatelessWidget {
  const _JournalCard({required this.ledger});

  final SignalLedger? ledger;

  @override
  Widget build(BuildContext context) {
    final trades = ledger?.trades.reversed.take(12).toList() ?? const <PaperTrade>[];
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Бумажный журнал раздела'),
          const SizedBox(height: 6),
          Text(
            'Каждая показанная идея автоматически проживается по реальным '
            'дневкам — форвард-проверка стратегии не зависит от того, входили '
            'ли вы. Журнал отдельный: со свингом не смешивается.',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
          const SizedBox(height: 8),
          if (trades.isEmpty)
            Text('Записей пока нет — появятся после первого пересчёта.',
                style: T.body(11.5, color: C.muted))
          else
            for (final trade in trades)
              Container(
                padding: const EdgeInsets.symmetric(vertical: 7),
                decoration: const BoxDecoration(
                  border: Border(bottom: BorderSide(color: C.divider)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(trade.symbol, style: T.mono(12, weight: 700)),
                    ),
                    Text(_status(trade), style: T.body(11, color: C.muted)),
                    const SizedBox(width: 10),
                    Text(
                      _result(trade),
                      style: T.mono(12,
                          weight: 700, color: toneColor(_tone(trade))),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }

  static String _status(PaperTrade t) => switch (t.status) {
        PaperStatus.pending => 'ждёт входа',
        PaperStatus.open => 'в позиции',
        PaperStatus.closed => t.outcome ?? 'закрыта',
        PaperStatus.cancelled => 'отменена',
      };

  static String _result(PaperTrade t) {
    final r = t.status == PaperStatus.closed ? t.resultR : t.unrealizedR;
    if (r == null) return '—';
    return '${r >= 0 ? '+' : '−'}${r.abs().toStringAsFixed(2).replaceAll('.', ',')}R';
  }

  static Tone _tone(PaperTrade t) {
    final r = t.status == PaperStatus.closed ? t.resultR : t.unrealizedR;
    if (r == null) return Tone.neutral;
    return r >= 0 ? Tone.positive : Tone.negative;
  }
}

class _BacktestCard extends StatelessWidget {
  const _BacktestCard({required this.controller});

  final dynamic controller;

  @override
  Widget build(BuildContext context) {
    final result = controller.investDesk?.investBacktest;
    final running = controller.investBacktestRunning as bool;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Бэктест дневной стратегии'),
          const SizedBox(height: 6),
          Text(
            'Только техника: ~3 года дневок по 40 самым ликвидным бумагам, те '
            'же правила сделки, что в свинге (издержки, безубыток после TP1, '
            'выход по времени). Фундаментал в прогоне не участвует — его '
            'историю API не отдаёт, а прогонять сегодняшний P/E по прошлому '
            'значило бы заглядывать в будущее.',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
          const SizedBox(height: 8),
          if (result != null) ...[
            Text(result.info as String, style: T.body(11, color: C.muted)),
            const SizedBox(height: 6),
            Row(
              children: [
                for (final stat in result.stats)
                  Expanded(
                    child: Column(
                      children: [
                        Text(stat.value as String, style: T.mono(15, weight: 800)),
                        Text(stat.label as String,
                            style: T.body(10, color: C.muted)),
                      ],
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 8),
          ] else
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text('Ещё не запускался.', style: T.body(11.5, color: C.muted)),
            ),
          Row(
            children: [
              Expanded(
                child: Pressable(
                  onTap: running ? null : () => controller.runInvestBacktest(),
                  child: Container(
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    decoration: BoxDecoration(
                      border: Border.all(color: C.borderHover),
                      borderRadius: BorderRadius.circular(R.inner),
                    ),
                    child: Center(
                      child: Text(running ? 'Считаем…' : 'Запустить бэктест',
                          style: T.body(12, weight: 800, color: C.accent)),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Pressable(
                  onTap: running ? null : () => controller.optimizeInvest(),
                  child: Container(
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    decoration: BoxDecoration(
                      border: Border.all(color: C.borderHover),
                      borderRadius: BorderRadius.circular(R.inner),
                    ),
                    child: Center(
                      child: Text('Подобрать параметры',
                          style: T.body(12, weight: 800, color: C.accent)),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _RejectionsCard extends StatelessWidget {
  const _RejectionsCard({required this.rejections});

  final List<RejectionGroup> rejections;

  @override
  Widget build(BuildContext context) {
    final total = rejections.fold<int>(0, (sum, g) => sum + g.count);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Где отсеялся рынок'),
          const SizedBox(height: 6),
          Text(
            'Сводка по причинам: $total бумаг не дошли до выдачи. Это карта '
            'работы скринера — видно, что именно его остановило, а не список '
            'из трёхсот строк.',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
          const SizedBox(height: 8),
          for (final group in rejections)
            Container(
              padding: const EdgeInsets.symmetric(vertical: 7),
              decoration: const BoxDecoration(
                border: Border(bottom: BorderSide(color: C.divider)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: Text(group.reason,
                            style: T.body(11.5, weight: 700, height: 1.3)),
                      ),
                      const SizedBox(width: 10),
                      Text('${group.count}', style: T.mono(13, weight: 800)),
                    ],
                  ),
                  const SizedBox(height: 2),
                  Text(
                    group.examples.join(' · ') +
                        (group.count > group.examples.length ? ' …' : ''),
                    style: T.mono(10, color: C.muted, height: 1.4),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Почему выдача пуста — с причиной, а не «фильтры не пройдены».
class _EmptyCard extends StatelessWidget {
  const _EmptyCard({required this.digest});

  final InvestDigest digest;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Идей сегодня нет'),
            const SizedBox(height: 6),
            Text(
              digest.regimeBlocksLongs
                  ? 'Индекс МосБиржи в нисходящей структуре. Лонг-стратегия по '
                      'акциям в таком рынке идей не выдаёт — это её правило, а '
                      'не сбой: покупать в падающем рынке значит платить за '
                      'чужой тренд. Кандидаты, которых остановил только режим, '
                      'ниже — в листе ожидания.'
                  : 'Ни одна бумага не набрала проходной сетап. Раздел не обязан '
                      'выдавать пять идей каждый день — он обязан не выдавать '
                      'плохие. Что именно отсеялось, видно в сводке ниже.',
              style: T.body(12, color: C.muted, height: 1.5),
            ),
          ],
        ),
      );
}

/// Лист ожидания: сетап есть, мешает рынок. Не рекомендация к покупке.
class _WatchlistCard extends StatelessWidget {
  const _WatchlistCard({required this.watchlist});

  final List<InvestIdea> watchlist;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('Лист ожидания'),
            const SizedBox(height: 6),
            Text(
              'У этих бумаг сетап собран, но вход закрыт режимом рынка. Это не '
              'рекомендация: покупать против индекса стратегия не станет. '
              'Список показывает, кто первым попадёт в выдачу, когда структура '
              'индекса развернётся.',
              style: T.body(10.5, color: C.muted, height: 1.4),
            ),
            const SizedBox(height: 8),
            for (final idea in watchlist)
              Container(
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: const BoxDecoration(
                  border: Border(bottom: BorderSide(color: C.divider)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(idea.signal.symbol, style: T.mono(12.5, weight: 700)),
                          Text(idea.signal.name,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: T.body(10.5, color: C.muted)),
                        ],
                      ),
                    ),
                    Text(
                      'вход ${idea.signal.entry.toStringAsFixed(idea.signal.priceDecimals).replaceAll('.', ',')}',
                      style: T.mono(11, color: C.muted),
                    ),
                    const SizedBox(width: 10),
                    Text('${idea.signal.score}/100',
                        style: T.mono(12, weight: 700, color: C.accent)),
                  ],
                ),
              ),
          ],
        ),
      );
}
