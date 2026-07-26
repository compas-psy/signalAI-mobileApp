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
import '../widgets/vector_icon.dart';

/// Экран «Идеи» — утренний дайджест (ТЗ §4: анализ в 10:10 МСК).
class IdeasScreen extends StatelessWidget {
  const IdeasScreen({super.key, required this.digest});

  final DailyDigest digest;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const _Header(),
          Expanded(
            child: ListView(
              padding: EdgeInsets.zero,
              children: [
                _DigestTitle(digest: digest),
                if (digest.stale) const _StaleBanner(),
                _RegimeCard(digest: digest),
                _EventsCard(events: digest.events),
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 18, 16, 8),
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
                  padding: const EdgeInsets.fromLTRB(16, 0, 16, 90),
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

class _Header extends StatelessWidget {
  const _Header();

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.fromLTRB(16, 12, 16, 10),
        decoration: const BoxDecoration(
          color: C.headerBg,
          border: Border(bottom: BorderSide(color: C.dividerSoft)),
        ),
        child: Row(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(8),
              child: Image.asset('assets/images/logo.jpg', width: 28, height: 28),
            ),
            const SizedBox(width: 10),
            Text.rich(
              TextSpan(
                text: 'Signal',
                style: T.jost(20),
                children: [TextSpan(text: 'AI', style: T.jost(20, color: C.accent))],
              ),
            ),
            const Spacer(),
            SizedBox(
              width: 36,
              height: 36,
              child: Stack(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: C.card,
                      shape: BoxShape.circle,
                      border: Border.all(color: C.border),
                    ),
                    child: const Center(
                      child: VectorIcon(Icons.bell, size: 17, color: C.textSecondary),
                    ),
                  ),
                  // Непрочитанные уведомления.
                  const Positioned(
                    top: 7,
                    right: 8,
                    child: SizedBox(
                      width: 7,
                      height: 7,
                      child: DecoratedBox(
                        decoration: BoxDecoration(color: C.accent, shape: BoxShape.circle),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}

class _DigestTitle extends StatelessWidget {
  const _DigestTitle({required this.digest});

  final DailyDigest digest;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(digest.title, style: T.jost(22)),
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
        margin: const EdgeInsets.fromLTRB(16, 6, 16, 0),
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
        margin: const EdgeInsets.fromLTRB(16, 10, 16, 0),
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
        margin: const EdgeInsets.fromLTRB(16, 8, 16, 0),
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
                              style: T.jost(16),
                            ),
                          ),
                          const SizedBox(width: 7),
                          DirectionBadge(
                            label: signal.direction.label,
                            color: directionColor(signal.direction),
                            background: directionBackground(signal.direction),
                          ),
                          const SizedBox(width: 7),
                          OutlineBadge(
                            label: signal.status.label,
                            color: working ? C.accent : C.muted,
                            borderColor:
                                working ? const Color(0x66FFD400) : C.border,
                          ),
                        ],
                      ),
                      const SizedBox(height: 2),
                      Text(
                        '${signal.name} · ${signal.market.label} · ${signal.horizonLabel}',
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
                    Text(signal.lastPrice, style: T.mono(13, weight: 600)),
                    Text(
                      signal.changeLabel,
                      style: T.mono(11, color: signal.changeUp ? C.green : C.red),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 11),
            _PriceStrip(signal: signal),
            const SizedBox(height: 9),
            Row(
              crossAxisAlignment: CrossAxisAlignment.center,
              children: [
                Expanded(
                  child: Wrap(
                    spacing: 5,
                    runSpacing: 5,
                    children: [for (final chip in signal.chips) TagChip(chip)],
                  ),
                ),
                const SizedBox(width: 5),
                OutlineBadge(
                  label: 'R:R ${signal.riskReward}',
                  color: C.accent,
                  borderColor: C.accentBorder,
                  background: C.accentFaint,
                  fontWeight: 700,
                  padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Полоска «Вход / SL / TP» внутри карточки идеи.
class _PriceStrip extends StatelessWidget {
  const _PriceStrip({required this.signal});

  final TradingSignal signal;

  @override
  Widget build(BuildContext context) {
    final decimals = signal.priceDecimals;
    return InsetBox(
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
      child: Row(
        children: [
          _pair('Вход ', fmtPrice(signal.entry, decimals), C.accent),
          const SizedBox(width: 12),
          _pair('SL ', fmtPrice(signal.stopLoss, decimals), C.red),
          const SizedBox(width: 12),
          Expanded(
            child: Text.rich(
              TextSpan(
                text: 'TP ',
                style: T.mono(11, color: C.muted),
                children: [
                  TextSpan(
                    text: signal.takeProfits
                        .map((tp) => fmtPrice(tp.price, decimals))
                        .join(' / '),
                    style: T.mono(11, weight: 600, color: C.green),
                  ),
                ],
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }

  Widget _pair(String label, String value, Color color) => Text.rich(
        TextSpan(
          text: label,
          style: T.mono(11, color: C.muted),
          children: [
            TextSpan(text: value, style: T.mono(11, weight: 600, color: color)),
          ],
        ),
      );
}
