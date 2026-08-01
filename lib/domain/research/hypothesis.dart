/// Гипотеза раннего сигнала (ТЗ Early Signals §16.1).
///
/// Не идея и не сделка. Идея говорит, когда входить: у неё есть зона входа,
/// стоп, цели и срок в днях. Гипотеза говорит, что изменилось в экономике
/// эмитента и за какой срок это должно проявиться в отчётности. Горизонт —
/// год-два, исполнять её нечем, и подтверждать кнопкой тоже.
///
/// Поэтому здесь нет ни цены, ни объёма, ни направления сделки. ТЗ §16.4
/// запрещает выдавать BUY, SELL, гарантированную доходность и размер
/// позиции; модель выражает этот запрет отсутствием полей, а не проверкой
/// в коде — поле, которого нет, невозможно заполнить по недосмотру.
///
/// Максимум, который система выдаёт, — «стоит изучить вручную». Это
/// состояние называется `diligence_ready`, и следующего за ним нет.
library;

/// Состояние гипотезы. Порядок — от наблюдения к готовности к разбору.
enum HypothesisState {
  observation('наблюдение', 'что-то изменилось, причина не построена'),
  earlyCandidate('ранний кандидат', 'есть сигнал и понятный путь до KPI'),
  confirmed('подтверждённая гипотеза', 'правило 3–2–1 выполнено'),
  diligenceReady('к углублённому анализу', 'риски инструмента проверены'),
  invalidated('опровергнута', 'сработало условие опровержения'),
  expired('срок вышел', 'окно эффекта закончилось без подтверждения'),
  rejected('отклонена', 'шум, дубликат или чужая сущность');

  const HypothesisState(this.label, this.meaning);

  final String label;
  final String meaning;

  /// Живые состояния — те, что ещё проверяются.
  bool get live => index <= diligenceReady.index;

  static HypothesisState parse(String? raw) => switch (raw) {
        'observation' => observation,
        'early_candidate' => earlyCandidate,
        'confirmed_hypothesis' => confirmed,
        'diligence_ready' => diligenceReady,
        'invalidated' => invalidated,
        'expired' => expired,
        _ => rejected,
      };
}

/// Направление экономического изменения — не сторона сделки.
enum HypothesisDirection {
  positive('улучшение'),
  negative('ухудшение'),
  neutral('без направления');

  const HypothesisDirection(this.label);

  final String label;

  static HypothesisDirection parse(String? raw) => switch (raw) {
        'positive' => positive,
        'negative' => negative,
        _ => neutral,
      };
}

/// Одно доказательство при гипотезе.
class HypothesisEvidence {
  const HypothesisEvidence({
    required this.role,
    this.claim = '',
    this.lineageRootId = '',
    this.independenceGroup = '',
  });

  /// support / contradict / context / falsifier.
  final String role;
  final String claim;
  final String lineageRootId;
  final String independenceGroup;

  bool get contradicts => role == 'contradict';

  String get roleLabel => switch (role) {
        'support' => 'подтверждает',
        'contradict' => 'противоречит',
        'falsifier' => 'опровергнет',
        _ => 'контекст',
      };

  factory HypothesisEvidence.fromJson(Map<String, dynamic> j) =>
      HypothesisEvidence(
        role: j['role'] as String? ?? 'context',
        claim: j['claim'] as String? ?? '',
        lineageRootId: j['lineage_root_id'] as String? ?? '',
        independenceGroup: j['independence_group'] as String? ?? '',
      );
}

/// Что должно измениться в отчётности, если гипотеза верна.
class TargetKpi {
  const TargetKpi({
    required this.metric,
    this.expectedDirection = '',
    this.window = '',
  });

  final String metric;
  final String expectedDirection;
  final String window;

  factory TargetKpi.fromJson(Map<String, dynamic> j) => TargetKpi(
        metric: j['metric'] as String? ?? '',
        expectedDirection: j['expected_direction'] as String? ?? '',
        window: switch (j['measurement_window']) {
          Map<String, dynamic> w => '${w['from'] ?? ''}–${w['to'] ?? ''}',
          _ => '',
        },
      );
}

class Hypothesis {
  const Hypothesis({
    required this.id,
    required this.title,
    required this.state,
    required this.direction,
    this.symbol = '',
    this.entityId = '',
    this.market = '',
    this.expectedLag = '',
    this.evidenceScore = 0,
    this.economicScore = 0,
    this.marketContextScore,
    this.marketContextState = 'unknown',
    this.researchPriority = 0,
    this.uniqueSources = 0,
    this.dataTypes = 0,
    this.confirmations = 0,
    this.causalPathValid = false,
    this.targetKpis = const [],
    this.evidence = const [],
    this.riskFlags = const [],
    this.falsifiers = const [],
    this.missingEvidence = const [],
    this.stateReason = '',
    this.expiresAt,
  });

  final String id;
  final String title;
  final HypothesisState state;
  final HypothesisDirection direction;
  final String symbol;
  final String entityId;
  final String market;

  /// За какой срок эффект должен проявиться. Без него утверждение нечем
  /// опровергнуть: «когда-нибудь» не проверяется.
  final String expectedLag;

  final double evidenceScore;
  final double economicScore;

  /// null означает «не измерено» и никогда не подменяется средним: у нас
  /// нет лицензированных данных о консенсусе, и делать вид, что есть,
  /// хуже, чем сказать «неизвестно».
  final double? marketContextScore;
  final String marketContextState;

  /// Приоритет проверки, а не ожидаемая доходность.
  final double researchPriority;

  final int uniqueSources;
  final int dataTypes;
  final int confirmations;
  final bool causalPathValid;

  final List<TargetKpi> targetKpis;
  final List<HypothesisEvidence> evidence;
  final List<String> riskFlags;
  final List<String> falsifiers;
  final List<String> missingEvidence;
  final String stateReason;
  final DateTime? expiresAt;

  /// Чего не хватает до подтверждения, человеческим языком.
  ///
  /// Считается на устройстве по тем же порогам, что на сервере: экран
  /// должен уметь объяснить состояние, а не только показать ярлык.
  List<String> get shortfall {
    if (state.index >= HypothesisState.confirmed.index) return const [];
    return [
      if (uniqueSources < 3) 'независимых источников $uniqueSources из 3',
      if (dataTypes < 2) 'типов данных $dataTypes из 2',
      if (confirmations < 2) 'подтверждений $confirmations из 2',
      if (!causalPathValid) 'причинная цепочка до KPI не построена',
    ];
  }

  factory Hypothesis.fromJson(Map<String, dynamic> j) {
    final gate = j['three_two_one'] as Map<String, dynamic>? ?? const {};
    return Hypothesis(
      id: j['id'] as String? ?? '',
      title: j['title'] as String? ?? '',
      state: HypothesisState.parse(j['state'] as String?),
      direction: HypothesisDirection.parse(j['direction'] as String?),
      symbol: j['symbol'] as String? ?? '',
      entityId: j['entity_id'] as String? ?? '',
      market: j['market'] as String? ?? '',
      expectedLag: j['expected_lag'] as String? ?? '',
      evidenceScore: _num(j['evidence_score']),
      economicScore: _num(j['economic_score']),
      marketContextScore:
          j['market_context_score'] == null ? null : _num(j['market_context_score']),
      marketContextState: j['market_context_state'] as String? ?? 'unknown',
      researchPriority: _num(j['research_priority']),
      uniqueSources: (gate['unique_lineage_roots'] as num?)?.toInt() ?? 0,
      dataTypes: (gate['independence_groups'] as num?)?.toInt() ?? 0,
      confirmations: (gate['confirmation_periods'] as num?)?.toInt() ?? 0,
      causalPathValid: gate['causal_path_valid'] as bool? ?? false,
      targetKpis: [
        for (final k in j['target_kpis'] as List<dynamic>? ?? const [])
          TargetKpi.fromJson(k as Map<String, dynamic>),
      ],
      evidence: [
        for (final e in j['evidence'] as List<dynamic>? ?? const [])
          HypothesisEvidence.fromJson(e as Map<String, dynamic>),
      ],
      riskFlags: [
        for (final f in j['risk_flags'] as List<dynamic>? ?? const []) '$f',
      ],
      falsifiers: [
        for (final f in j['falsifiers'] as List<dynamic>? ?? const [])
          switch (f) {
            Map<String, dynamic> m => m['description'] as String? ?? '',
            _ => '$f',
          },
      ],
      missingEvidence: [
        for (final m in j['missing_evidence'] as List<dynamic>? ?? const []) '$m',
      ],
      stateReason: j['state_reason'] as String? ?? '',
      expiresAt: DateTime.tryParse(j['expires_at'] as String? ?? ''),
    );
  }

  static double _num(Object? raw) => switch (raw) {
        num n => n.toDouble(),
        String s => double.tryParse(s) ?? 0,
        _ => 0,
      };
}

/// Источник, из которого сбор невозможен, и почему.
class BlockedSource {
  const BlockedSource({
    required this.sourceId,
    required this.name,
    required this.status,
    required this.note,
  });

  final String sourceId;
  final String name;
  final String status;
  final String note;

  String get statusLabel => switch (status) {
        'requires_contract' => 'нужен договор',
        'manual_only' => 'только вручную',
        'prohibited' => 'запрещён условиями',
        _ => 'не проверен',
      };

  factory BlockedSource.fromJson(Map<String, dynamic> j) => BlockedSource(
        sourceId: j['source_id'] as String? ?? '',
        name: j['name'] as String? ?? '',
        status: j['status'] as String? ?? 'unknown',
        note: j['note'] as String? ?? '',
      );
}

/// Состояние контура: гипотезы и то, почему их столько.
class ResearchState {
  const ResearchState({
    this.hypotheses = const [],
    this.totalSources = 0,
    this.freeRoute = 0,
    this.awaitingTermsCheck = const [],
    this.manualOnly = const [],
    this.unavailable = const [],
    this.connectOrder = const [],
    this.reason = '',
    this.unavailableReason,
  });

  const ResearchState.unavailable(String reason)
      : hypotheses = const [],
        totalSources = 0,
        freeRoute = 0,
        awaitingTermsCheck = const [],
        manualOnly = const [],
        unavailable = const [],
        connectOrder = const [],
        reason = '',
        unavailableReason = reason;

  final List<Hypothesis> hypotheses;
  final int totalSources;

  /// Сколько источников имеет бесплатный официальный маршрут.
  final int freeRoute;

  /// Из них — сколько ждут проверки условий использования. До неё сбор не
  /// запускается, и это единственное, что владелец может сделать сегодня.
  final List<String> awaitingTermsCheck;

  final List<String> manualOnly;
  final List<BlockedSource> unavailable;
  final List<String> connectOrder;
  final String reason;
  final String? unavailableReason;

  bool get isAvailable => unavailableReason == null;

  factory ResearchState.fromJson(Map<String, dynamic> j) {
    final status = j['status'] as Map<String, dynamic>? ?? const {};
    return ResearchState(
      hypotheses: [
        for (final h in j['hypotheses'] as List<dynamic>? ?? const [])
          Hypothesis.fromJson(h as Map<String, dynamic>),
      ],
      totalSources: (status['total_sources'] as num?)?.toInt() ?? 0,
      freeRoute: (status['free_route'] as num?)?.toInt() ?? 0,
      awaitingTermsCheck: [
        for (final s in status['awaiting_terms_check'] as List<dynamic>? ?? const [])
          '$s',
      ],
      manualOnly: [
        for (final s in status['manual_only'] as List<dynamic>? ?? const []) '$s',
      ],
      unavailable: [
        for (final s in status['unavailable'] as List<dynamic>? ?? const [])
          BlockedSource.fromJson(s as Map<String, dynamic>),
      ],
      connectOrder: [
        for (final s in status['connect_order'] as List<dynamic>? ?? const []) '$s',
      ],
      reason: status['reason'] as String? ?? '',
    );
  }
}
