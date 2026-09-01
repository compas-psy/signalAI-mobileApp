import '../enums.dart';
import 'signal.dart';
import 'trade_plan_geometry.dart';

/// Котировка в строке режима рынка (ТЗ §9: RI/Si/BR + BTC одной строкой).
class RegimeQuote {
  const RegimeQuote({required this.name, required this.value, required this.tone});

  final String name;

  /// Готовая подпись: «−0,42%», «28,4».
  final String value;
  final Tone tone;

  Map<String, dynamic> toJson() => {'name': name, 'value': value, 'tone': tone.name};

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
    this.sourceNote,
    this.rejections = const [],
    this.stale = false,
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

  /// Происхождение данных: где и когда посчитаны идеи. null — не показывается.
  final String? sourceNote;

  /// Причины отбраковки кандидатов последнего прогона — чтобы было видно,
  /// что скринер реально работал, а не выдал заготовку.
  final List<String> rejections;

  /// Данные показаны из кэша: обновить не удалось, уровни могли устареть.
  final bool stale;

  /// Тот же дайджест, но с пометкой, что он устарел и обновить не удалось.
  ///
  /// Показывать прошлые идеи с честной подписью лучше, чем пустой экран
  /// «данные недоступны»: уровни всё ещё видны, а их возраст назван прямо.
  DailyDigest withStaleNote(String note) => DailyDigest(
        title: title,
        subtitle: subtitle,
        deliveryBadges: deliveryBadges,
        regime: regime,
        regimeNote: regimeNote,
        events: events,
        signals: signals,
        signalsQuota: signalsQuota,
        sourceNote: sourceNote == null ? note : '$note\n\n$sourceNote',
        rejections: rejections,
        stale: true,
      );

  DailyDigest copyWith({List<TradingSignal>? signals}) => DailyDigest(
        title: title,
        subtitle: subtitle,
        deliveryBadges: deliveryBadges,
        regime: regime,
        regimeNote: regimeNote,
        events: events,
        signals: signals ?? this.signals,
        signalsQuota: signalsQuota,
        sourceNote: sourceNote,
        rejections: rejections,
        stale: stale,
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
        // Старые версии приложения могли сохранить идею до появления
        // структурного инварианта Entry/SL/TP. После обновления такой кэш
        // нельзя продолжать показывать как исполнимый торговый план: цены не
        // переставляем и не «лечим», а честно отбрасываем весь сигнал.
        signals: (j['signals'] as List<dynamic>? ?? const [])
            .map((e) => TradingSignal.fromJson(e as Map<String, dynamic>))
            .where((signal) => signal.tradePlanBlockers().isEmpty)
            .toList(growable: false),
        signalsQuota: j['signals_quota'] as String? ?? '',
        sourceNote: j['source_note'] as String?,
        rejections: (j['rejections'] as List<dynamic>? ?? const [])
            .map((e) => e as String)
            .toList(growable: false),
      );

  Map<String, dynamic> toJson() => {
        'title': title,
        'subtitle': subtitle,
        'delivery_badges': deliveryBadges,
        'regime': [for (final q in regime) q.toJson()],
        'regime_note': regimeNote,
        'events': [for (final e in events) e.toJson()],
        'signals': [for (final s in signals) s.toJson()],
        'signals_quota': signalsQuota,
        'source_note': sourceNote,
        'rejections': rejections,
      };
}