import 'package:flutter/widgets.dart';

import '../../domain/invest/invest_models.dart';
import '../../domain/ledger/signal_ledger.dart';
import '../../domain/enums.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
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
        const ScreenHeader(title: 'Инвест'),
        Expanded(
          child: digest == null
              ? _Pending(controller: controller)
              : ListView(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 90),
                  children: [
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
                    if (digest.watchlist.isNotEmpty) ...[
                      _WatchlistCard(watchlist: digest.watchlist),
                      const SizedBox(height: 12),
                    ],
                    _JournalCard(ledger: controller.investDesk?.investLedger),
                    const SizedBox(height: 12),
                    _BacktestCard(controller: controller),
                    if (digest.rejections.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      _RejectionsCard(rejections: digest.rejections),
                    ],
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
          Row(
            children: [
              const Expanded(child: SectionLabel('Среднесрок · акции РФ')),
              const OutlineBadge(
                label: 'ЛОНГИ · 1–3 МЕС',
                color: C.accent,
                borderColor: C.accent,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            'Пересчитано $when МСК · доска TQBR: ${digest.universeSize} бумаг → '
            'ликвидных ${digest.liquidSize} → акций с историей ${digest.tradableSize}. '
            'Пересчёт — ночью раз в день. Техника проверяется бэктестом; '
            'фундаментальный паспорт — текущий срез Invest API, в отборе идей '
            'не участвует. Исполнение — руками.',
            style: T.body(11, color: C.muted, height: 1.5),
          ),
          if (digest.regimeNote.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              digest.regimeNote,
              style: T.body(11.5,
                  color: digest.regimeBlocksLongs ? C.red : C.green, height: 1.4),
            ),
          ],
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
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(signal.symbol, style: T.jost(18)),
                    Text(signal.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: T.body(11, color: C.muted)),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(signal.lastPrice, style: T.mono(14, weight: 700)),
                  Text(signal.changeLabel,
                      style: T.mono(11,
                          color: signal.changeUp ? C.green : C.red)),
                ],
              ),
              const SizedBox(width: 10),
              OutlineBadge(
                label: '${signal.score}/100',
                color: C.accent,
                borderColor: C.accent,
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (signal.chart != null)
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
          InsetBox(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            radius: R.inner,
            child: Column(
              children: [
                KeyValueRow(name: 'Вход · лимит', value: price(signal.entry)),
                KeyValueRow(
                  name: 'Стоп-лосс',
                  value: price(signal.stopLoss),
                  valueStyle: T.mono(12, weight: 600, color: C.red),
                ),
                KeyValueRow(
                  name: signal.takeProfits.map((tp) => tp.label).join(' / '),
                  value:
                      signal.takeProfits.map((tp) => price(tp.price)).join(' / '),
                  valueStyle: T.mono(12, weight: 600, color: C.green),
                ),
                KeyValueRow(name: 'R:R до TP2', value: signal.riskReward),
                const KeyValueRow(
                    name: 'Горизонт', value: 'до 60 торговых дней'),
              ],
            ),
          ),
          const SizedBox(height: 10),
          Text('Почему эта идея', style: T.body(12, weight: 800)),
          const SizedBox(height: 4),
          Text(signal.note, style: T.body(11.5, color: C.textSecondary, height: 1.5)),
          const SizedBox(height: 8),
          for (final factor in signal.factors)
            Padding(
              padding: const EdgeInsets.only(top: 3),
              child: Text('· ${factor.name}: ${factor.text}',
                  style: T.body(10.5, color: C.muted, height: 1.4)),
            ),
          const SizedBox(height: 10),
          _Passport(passport: idea.passport, lastPrice: signal.lastPrice),
        ],
      ),
    );
  }
}

class _Passport extends StatelessWidget {
  const _Passport({required this.passport, required this.lastPrice});

  final FundamentalsPassport? passport;
  final String lastPrice;

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
    String cap(double? v) {
      if (v == null) return '—';
      if (v >= 1e12) return '${(v / 1e12).toStringAsFixed(1).replaceAll('.', ',')} трлн ₽';
      return '${(v / 1e9).toStringAsFixed(0)} млрд ₽';
    }

    final reportDate = p.nextReportDate;
    final daysToReport = reportDate?.difference(DateTime.now()).inDays;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(child: Text('Фундаментальный паспорт', style: T.body(12, weight: 800))),
            Text('срез, не ранжир', style: T.body(9.5, color: C.muted)),
          ],
        ),
        const SizedBox(height: 6),
        InsetBox(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          radius: R.inner,
          child: Column(
            children: [
              KeyValueRow(name: 'Капитализация', value: cap(p.marketCap)),
              KeyValueRow(name: 'P/E · P/B', value: '${num(p.peTtm)} · ${num(p.pbTtm)}'),
              KeyValueRow(name: 'EV/EBITDA', value: num(p.evToEbitda)),
              KeyValueRow(name: 'ROE · маржа', value: '${pct(p.roe)} · ${pct(p.netMargin)}'),
              KeyValueRow(name: 'Долг/EBITDA', value: num(p.debtToEbitda)),
              KeyValueRow(
                  name: 'Дивиденды (TTM · форвард)',
                  value: '${pct(p.dividendYieldTtm)} · ${pct(p.forwardDividendYield)}'),
              KeyValueRow(
                  name: 'Рост выручки (1г · 5л)',
                  value: '${pct(p.revenueGrowthOneYear)} · ${pct(p.revenueGrowthFiveYears)}'),
              if (p.consensus != null || p.targetPrice != null)
                KeyValueRow(
                  name: 'Аналитики',
                  value:
                      '${_consensusLabel(p.consensus)} · цель ${num(p.targetPrice, digits: 2)}'
                      '${p.buyCount == null ? '' : ' · ${p.buyCount}/${p.holdCount ?? 0}/${p.sellCount ?? 0}'}',
                ),
            ],
          ),
        ),
        if (daysToReport != null && daysToReport <= 14)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text(
              'Отчётность через $daysToReport дн. '
              '(${reportDate!.day.toString().padLeft(2, '0')}.${reportDate.month.toString().padLeft(2, '0')}) — '
              'гэп на публикации вероятен, учитывайте при входе.',
              style: T.body(10.5, color: C.accent, height: 1.4),
            ),
          ),
        Padding(
          padding: const EdgeInsets.only(top: 6),
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
