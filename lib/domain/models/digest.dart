import '../enums.dart';
import 'signal.dart';

/// Котировка в строке режима рынка (ТЗ §9: RI/Si/BR + BTC одной строкой).
class RegimeQuote {
  const RegimeQuote({required this.name, required this.value, required this.tone});

  final String name;

  /// Готовая подпись: «−0,42%», «28,4».
  final String value;
  final Tone tone;

  factory RegimeQuote.fromJson(Map<String, dynamic> j) => RegimeQuote(
        name: j['name'] as String,
        value: j['value'] as String,
        tone: switch (j['tone'] as String? ?? 'neutral') {
          'positive' => Tone.positive,
          'negative' => Tone.negative,
          'accent' => Tone.accent,
          _ => Tone.neutral,
        },
      );
}

/// Утренний дайджест — главный экран (ТЗ §4: анализ 10:10 МСК).
class DailyDigest {
  const DailyDigest({
    required this.title,
    required this.subtitle,
    required this.deliveryBadges,
    required this.regime,
    required this.regimeNote,
    required this.events,
    required this.signals,
    required this.signalsQuota,
  });

  /// «Утренний дайджест».
  final String title;

  /// «Сб, 25 июля · 10:10 МСК».
  final String subtitle;

  /// Подтверждённые каналы доставки: «TG ✓», «MAX ✓».
  final List<String> deliveryBadges;

  final List<RegimeQuote> regime;

  /// Текстовый вывод по режиму рынка.
  final String regimeNote;

  /// События по активным идеям.
  final List<MarketEvent> events;

  final List<TradingSignal> signals;

  /// «5 из 5» — сколько идей отдано из дневного лимита (ТЗ §5.4).
  final String signalsQuota;

  DailyDigest copyWith({List<TradingSignal>? signals}) => DailyDigest(
        title: title,
        subtitle: subtitle,
        deliveryBadges: deliveryBadges,
        regime: regime,
        regimeNote: regimeNote,
        events: events,
        signals: signals ?? this.signals,
        signalsQuota: signalsQuota,
      );

  factory DailyDigest.fromJson(Map<String, dynamic> j) => DailyDigest(
        title: j['title'] as String? ?? 'Утренний дайджест',
        subtitle: j['subtitle'] as String? ?? '',
        deliveryBadges: (j['delivery_badges'] as List<dynamic>? ?? const [])
            .map((e) => e as String)
            .toList(growable: false),
        regime: (j['regime'] as List<dynamic>? ?? const [])
            .map((e) => RegimeQuote.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        regimeNote: j['regime_note'] as String? ?? '',
        events: (j['events'] as List<dynamic>? ?? const [])
            .map((e) => MarketEvent.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        signals: (j['signals'] as List<dynamic>? ?? const [])
            .map((e) => TradingSignal.fromJson(e as Map<String, dynamic>))
            .toList(growable: false),
        signalsQuota: j['signals_quota'] as String? ?? '',
      );
}
