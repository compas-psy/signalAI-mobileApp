import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/digest.dart';
import '../../domain/models/signal.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../tone.dart';
import '../widgets/common.dart';
import '../widgets/confluence_ring.dart';
import '../widgets/level_strip.dart';
import '../widgets/vector_icon.dart';

/// Экран «Идеи» — утренний дайджест (ТЗ §4: анализ в 10:10 МСК).
class IdeasScreen extends StatelessWidget {
  const IdeasScreen({super.key, required this.digest});

  final DailyDigest digest;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            child: ListView(
              padding: EdgeInsets.zero,
              children: [
                _DigestTitle(digest: digest),
                if (digest.stale) const _StaleBanner(),
                _RegimeCard(digest: digest),
                _EventsCard(events: digest.events),
                Padding(
                  padding: const EdgeInsets.fromLTRB(S.screen, 18, S.screen, 8),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.baseline,
                    textBaseline: TextBaseline.alphabetic,
                    children: [
                      const Expanded(child: SectionLabel('Идеи на сегодня')),
                      Text(
                        digest.signalsQuota,
                        style: T.body(12, weight: 700, color: C.accent),
                      ),
                    ],
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.fromLTRB(S.screen, 0, S.screen, 90),
                  child: Column(
                    children: [
                      if (digest.signals.isEmpty)
                        const _NoIdeasCard()
                      else
                        for (final signal in digest.signals) ...[
                          IdeaCard(signal: signal),
                          if (signal != digest.signals.last) const SizedBox(height: 10),
                        ],
                      if (digest.sourceNote != null || digest.rejections.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        _ProvenanceCard(digest: digest),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
        ],
      );
}

class _DigestTitle extends StatelessWidget {
  const _DigestTitle({required this.digest});

  final DailyDigest digest;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(S.screen, 14, S.screen, 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(digest.title, style: T.jost(23)),
                  const SizedBox(height: 2),
                  Text(digest.subtitle, style: T.body(12, color: C.muted)),
                ],
              ),
            ),
            for (final badge in digest.deliveryBadges) ...[
              const SizedBox(width: 6),
              OutlineBadge(
                label: badge,
                color: C.green,
                borderColor: C.greenBorder,
                background: C.greenFaint,
                fontWeight: 700,
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                radius: R.pill,
              ),
            ],
          ],
        ),
      );
}

class _RegimeCard extends StatelessWidget {
  const _RegimeCard({required this.digest});

  final DailyDigest digest;

  @override
  Widget build(BuildContext context) => SectionCard(
        margin: const EdgeInsets.fromLTRB(S.screen, 6, S.screen, 0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: [
                for (final quote in digest.regime)
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
                    decoration: BoxDecoration(
                      color: C.chip,
                      borderRadius: BorderRadius.circular(R.chipLg),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.baseline,
                      textBaseline: TextBaseline.alphabetic,
                      children: [
                        Text(quote.name, style: T.body(11, weight: 600, color: C.muted)),
                        const SizedBox(width: 5),
                        Text(
                          quote.value,
                          style: T.mono(11, weight: 600, color: toneColor(quote.tone)),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              digest.regimeNote,
              style: T.body(12, color: C.textSecondary, height: 1.5),
            ),
          ],
        ),
      );
}

class _EventsCard extends StatelessWidget {
  const _EventsCard({required this.events});

  final List<MarketEvent> events;

  @override
  Widget build(BuildContext context) => SectionCard(
        margin: const EdgeInsets.fromLTRB(S.screen, 10, S.screen, 0),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const SectionLabel('События по активным идеям'),
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
                    width: 40,
                    child: Text(event.time, style: T.mono(11, color: C.muted)),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(event.text, style: T.body(12, color: C.textSoft)),
                  ),
                  if (event.affects != null) ...[
                    const SizedBox(width: 10),
                    Text(event.affects!, style: T.body(10, color: C.muted)),
                  ],
                ],
              ),
            ],
          ],
        ),
      );
}

/// Данные показаны из кэша: обновить не удалось. Молчать об этом нельзя —
/// уровни могли устареть, а решение принимает человек.
class _StaleBanner extends StatelessWidget {
  const _StaleBanner();

  @override
  Widget build(BuildContext context) => Container(
        margin: const EdgeInsets.fromLTRB(S.screen, 8, S.screen, 0),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: const Color(0x1AFFD400),
          border: Border.all(color: const Color(0x59FFD400)),
          borderRadius: BorderRadius.circular(R.inner),
        ),
        child: Row(
          children: [
            const VectorIcon(Icons.shield, size: 14, color: C.accent),
            const SizedBox(width: 9),
            Expanded(
              child: Text(
                'Данные из кэша: обновить не удалось. Проверьте связь — '
                'Настройки → Диагностика данных.',
                style: T.body(11, color: C.accent, height: 1.4),
              ),
            ),
          ],
        ),
      );
}

/// Честная пустая выдача: идей нет — и это результат, а не сбой.
class _NoIdeasCard extends StatelessWidget {
  const _NoIdeasCard();

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Сегодня идей нет', style: T.body(14, weight: 700)),
            const SizedBox(height: 4),
            Text(
              'Скринер отработал, но ни один кандидат не прошёл фильтры. '
              'Это нормально: отсутствие сделки — тоже решение. Причины '
              'отбраковки — ниже.',
              style: T.body(11, color: C.muted, height: 1.5),
            ),
          ],
        ),
      );
}

/// Происхождение данных: где посчитано, что отбраковано, кнопка пересчёта.
///
/// Карточка отвечает на вопрос «это муляж или реальные идеи?» прямо в
/// интерфейсе: видно время расчёта, источники и работу фильтров.
class _ProvenanceCard extends StatefulWidget {
  const _ProvenanceCard({required this.digest});

  final DailyDigest digest;

  @override
  State<_ProvenanceCard> createState() => _ProvenanceCardState();
}

class _ProvenanceCardState extends State<_ProvenanceCard> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final digest = widget.digest;
    final rejections = digest.rejections;
    final shown = _expanded ? rejections : rejections.take(4).toList();

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Откуда эти идеи'),
          if (digest.sourceNote != null) ...[
            const SizedBox(height: 6),
            Text(digest.sourceNote!, style: T.body(11, color: C.textSecondary, height: 1.5)),
          ],
          if (rejections.isNotEmpty) ...[
            const SizedBox(height: 10),
            Text(
              'Отбраковано кандидатов: ${rejections.length}',
              style: T.body(11, weight: 700, color: C.muted),
            ),
            const SizedBox(height: 4),
            for (final line in shown)
              Padding(
                padding: const EdgeInsets.only(top: 3),
                child: Text('· $line', style: T.mono(10.5, color: C.dim)),
              ),
            if (rejections.length > 4) ...[
              const SizedBox(height: 6),
              Pressable(
                onTap: () => setState(() => _expanded = !_expanded),
                child: Text(
                  _expanded ? 'Свернуть' : 'Показать все ${rejections.length}',
                  style: T.body(11, weight: 700, color: C.accent),
                ),
              ),
            ],
          ],
          const SizedBox(height: 10),
          Pressable(
            onTap: () => controller.refreshDigest(force: true),
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 10),
              decoration: BoxDecoration(
                border: Border.all(color: C.borderHover),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: Center(
                child: Text(
                  'Пересчитать идеи',
                  style: T.body(12, weight: 800, color: C.accent),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Карточка идеи в списке дайджеста.
class IdeaCard extends StatelessWidget {
  const IdeaCard({super.key, required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    final working = signal.status.isWorking;

    final decimals = signal.priceDecimals;
    // Сетап одной строкой: то же, что раньше было россыпью чипов, но читается
    // слева направо как фраза и не отнимает у карточки третью строку.
    final setup = [
      if (signal.chips.isNotEmpty) signal.chips.join(' + '),
      signal.market.label,
      signal.horizonLabel,
    ].join(' · ');

    return Pressable(
      onTap: () => controller.openSignal(signal.id),
      child: SectionCard(
        padding: const EdgeInsets.fromLTRB(14, 13, 14, 13),
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
                      if (working) ...[
                        const SizedBox(width: 7),
                        Text(
                          '● ${signal.status.label}',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: T.body(10, weight: 700, color: C.accent),
                        ),
                      ],
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(signal.lastPrice, style: T.mono(13.5, weight: 600)),
                    Text(
                      signal.changeLabel,
                      style: T.mono(11, color: signal.changeUp ? C.green : C.red),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 7),
            Text(
              setup,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: T.body(11.5, color: C.muted),
            ),
            const SizedBox(height: 10),
            LevelStrip(
              entry: fmtPrice(signal.entry, decimals),
              stop: fmtPrice(signal.stopLoss, decimals),
              targets: [
                for (final tp in signal.takeProfits) fmtPrice(tp.price, decimals),
              ],
              riskReward: '${signal.riskReward} R:R',
            ),
          ],
        ),
      ),
    );
  }
}
