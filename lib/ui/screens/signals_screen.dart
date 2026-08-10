import 'package:flutter/material.dart' show RefreshIndicator;
import 'package:flutter/widgets.dart';

import '../../data/api/equity_radar_client.dart';
import '../../domain/research/equity_radar.dart';
import '../../domain/research/hypothesis.dart';
import '../../state/app_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';

/// Ежедневный радар российских акций в «Портфель → Сигналы».
///
/// В отличие от торговых идей, здесь компания не исчезает только потому, что
/// сегодня нет готового сетапа или подтверждённой research-гипотезы. Владелец
/// видит весь отслеживаемый equity-universe сверху вниз: сильные кандидаты,
/// нейтральные и слабые. Это рейтинг для дальнейшего изучения, а не BUY/SELL.
class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key, required this.controller});

  final AppController controller;

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  final EquityRadarClient _client = EquityRadarClient();
  EquityRadarState? _radar;
  Object? _error;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _load();
    widget.controller.loadResearch();
  }

  Future<void> _load() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final value = await _client.load();
      if (!mounted) return;
      setState(() => _radar = value);
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = error);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final radar = _radar;
    if (radar == null && _loading) {
      return const _StateNote(
        title: 'Рейтинг компаний',
        text: 'Загружаю ежедневный срез российских акций…',
      );
    }
    if (radar == null) {
      return _StateNote(
        title: 'Рейтинг компаний недоступен',
        text: _error == null
            ? 'Сервер ещё не вернул ежедневный срез.'
            : 'Не удалось получить рейтинг: $_error',
        action: 'Повторить',
        onTap: _load,
      );
    }

    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        physics: const AlwaysScrollableScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
        children: [
          _RadarHeader(radar: radar, loading: _loading),
          const SizedBox(height: 12),
          if (radar.cards.isEmpty)
            const _StateNote(
              title: 'Нет акций для ранжирования',
              text: 'В движке пока нет отслеживаемых российских акций. Это состояние данных, а не «всё плохо».',
            )
          else
            for (var i = 0; i < radar.cards.length; i++) ...[
              _CompanyCard(card: radar.cards[i], rank: i + 1),
              const SizedBox(height: 10),
            ],
          const SizedBox(height: 4),
          _ResearchHealth(state: widget.controller.research),
        ],
      ),
    );
  }
}

class _RadarHeader extends StatelessWidget {
  const _RadarHeader({required this.radar, required this.loading});

  final EquityRadarState radar;
  final bool loading;

  @override
  Widget build(BuildContext context) {
    final asOf = radar.asOf;
    final date = asOf == null
        ? 'дата дневного среза неизвестна'
        : '${asOf.day.toString().padLeft(2, '0')}.${asOf.month.toString().padLeft(2, '0')}.${asOf.year}';
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Ежедневный рейтинг компаний')),
              if (loading) Text('обновляю…', style: T.mono(9.5, color: C.faint)),
            ],
          ),
          const SizedBox(height: 7),
          Text(
            'Сверху — то, что сейчас имеет смысл разбирать глубже. Ниже — нейтральные и слабые бумаги. '
            'Слабая компания не удаляется из списка: завтра она может подняться после новых данных.',
            style: T.body(11.5, color: C.textSecondary, height: 1.5),
          ),
          const SizedBox(height: 7),
          Text(
            '$date · ${radar.cards.length} компаний · ${radar.method}',
            style: T.mono(9.5, color: C.faint),
          ),
          const SizedBox(height: 5),
          Text(
            'Рейтинг пересчитывается после новой закрытой дневной свечи. Это фильтр внимания, не торговая команда.',
            style: T.body(10.5, color: C.faint, height: 1.4),
          ),
        ],
      ),
    );
  }
}

class _CompanyCard extends StatelessWidget {
  const _CompanyCard({required this.card, required this.rank});

  final EquityRadarCard card;
  final int rank;

  Color get tone {
    if (card.score >= 75) return C.green;
    if (card.score >= 60) return C.accent;
    if (card.score >= 45) return C.textSecondary;
    if (card.score >= 30) return C.warning;
    return C.red;
  }

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Container(
                  width: 28,
                  height: 28,
                  alignment: Alignment.center,
                  decoration: BoxDecoration(
                    color: C.inset,
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: C.border),
                  ),
                  child: Text('$rank', style: T.mono(10, color: C.muted)),
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(card.symbol, style: T.jost(16, weight: 700, color: C.text)),
                      if (card.title.isNotEmpty && card.title != card.symbol)
                        Text(card.title, style: T.body(10.5, color: C.muted)),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      card.score.toStringAsFixed(0),
                      style: T.mono(22, weight: 700, color: tone),
                    ),
                    Text(card.label, style: T.body(9.5, weight: 700, color: tone)),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 11),
            Row(
              children: [
                _Score(label: 'фундамент', value: card.researchScore),
                const SizedBox(width: 8),
                _Score(label: 'техника D1', value: card.technicalScore),
                const SizedBox(width: 8),
                _Score(label: 'ликвидность', value: card.liquidityScore),
              ],
            ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(child: _Change(label: '20 дней', value: card.return20d)),
                const SizedBox(width: 8),
                Expanded(child: _Change(label: '60 дней', value: card.return60d)),
                const SizedBox(width: 8),
                Expanded(
                  child: _Mini(
                    label: 'ранний сигнал',
                    value: card.hasResearchHypothesis ? 'есть' : 'нет',
                    color: card.hasResearchHypothesis ? C.green : C.faint,
                  ),
                ),
              ],
            ),
            if (card.thesis.isNotEmpty) ...[
              const SizedBox(height: 11),
              Text(card.thesis, style: T.body(11.5, color: C.text, height: 1.45)),
            ],
            if (card.reasons.isNotEmpty) ...[
              const SizedBox(height: 8),
              for (final reason in card.reasons.take(3))
                Padding(
                  padding: const EdgeInsets.only(bottom: 2),
                  child: Text('· $reason', style: T.body(10.5, color: C.textSecondary, height: 1.4)),
                ),
            ],
            if (card.warnings.isNotEmpty) ...[
              const SizedBox(height: 7),
              Text(
                card.warnings.join(' · '),
                style: T.body(9.5, color: C.warning, height: 1.35),
              ),
            ],
          ],
        ),
      );
}

class _Score extends StatelessWidget {
  const _Score({required this.label, required this.value});
  final String label;
  final double value;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Container(
          padding: const EdgeInsets.fromLTRB(9, 8, 9, 8),
          decoration: BoxDecoration(
            color: C.inset,
            borderRadius: BorderRadius.circular(9),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(value.toStringAsFixed(0), style: T.mono(13, weight: 700, color: C.textSoft)),
              Text(label, style: T.body(9, color: C.faint)),
            ],
          ),
        ),
      );
}

class _Change extends StatelessWidget {
  const _Change({required this.label, required this.value});
  final String label;
  final double? value;

  @override
  Widget build(BuildContext context) {
    final v = value;
    final color = v == null
        ? C.faint
        : v > 0
            ? C.green
            : v < 0
                ? C.red
                : C.muted;
    final text = v == null ? '—' : '${v > 0 ? '+' : ''}${v.toStringAsFixed(1)}%';
    return _Mini(label: label, value: text, color: color);
  }
}

class _Mini extends StatelessWidget {
  const _Mini({required this.label, required this.value, required this.color});
  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(value, style: T.mono(10.5, weight: 700, color: color)),
          Text(label, style: T.body(8.5, color: C.faint)),
        ],
      );
}

class _ResearchHealth extends StatelessWidget {
  const _ResearchHealth({required this.state});

  final ResearchState? state;

  @override
  Widget build(BuildContext context) {
    final s = state;
    if (s == null) return const SizedBox.shrink();
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Контур ранних фундаментальных сигналов'),
          const SizedBox(height: 6),
          Text(
            s.isAvailable
                ? 'Живых гипотез ${s.hypotheses.length}. Они усиливают или ослабляют карточки выше, но их отсутствие больше не делает раздел пустым.'
                : 'Контур гипотез временно недоступен: ${s.unavailableReason}',
            style: T.body(10.5, color: C.faint, height: 1.45),
          ),
        ],
      ),
    );
  }
}

class _StateNote extends StatelessWidget {
  const _StateNote({
    required this.title,
    required this.text,
    this.action,
    this.onTap,
  });

  final String title;
  final String text;
  final String? action;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) => Center(
        child: Padding(
          padding: const EdgeInsets.all(S.screen),
          child: SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(title, style: T.jost(16, weight: 700, color: C.text)),
                const SizedBox(height: 6),
                Text(text, style: T.body(11.5, color: C.muted, height: 1.5)),
                if (action != null && onTap != null) ...[
                  const SizedBox(height: 12),
                  GestureDetector(
                    onTap: onTap,
                    child: Text(action!, style: T.body(11.5, weight: 700, color: C.accent)),
                  ),
                ],
              ],
            ),
          ),
        ),
      );
}
