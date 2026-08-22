import '../enums.dart';
import 'evidence.dart';
import 'idea_state.dart';
import 'quality_score.dart';
import 'trade_plan.dart';

/// Стратегия из методологии v1.0 (ТЗ §14).
enum SetupStrategy {
  trendPullback('Trend Pullback Continuation', 'Основная', 0.55),
  breakoutRetest('Breakout & Retest', 'Продолжение после диапазона', 0.30),
  wyckoffReversal('Wyckoff Reversal', 'Редкий разворот', 0.15);

  const SetupStrategy(this.label, this.role, this.expectedShare);

  final String label;
  final String role;

  /// Ожидаемая доля потока идей (ТЗ §14). Отклонение — повод для разбора,
  /// а не для тихой подгонки: если разворотов больше половины, движок
  /// торгует не то, что заявлено.
  final double expectedShare;

  static SetupStrategy parse(String? name) => SetupStrategy.values.firstWhere(
        (s) => s.name == name,
        orElse: () => SetupStrategy.trendPullback,
      );
}

/// Готовность идеи именно к решению владельца.
///
/// Это не дубликат [IdeaState]. Состояние описывает жизненный цикл идеи, а
/// готовность — ответ выдачи `/ideas/today`: можно ли действовать сейчас или
/// идея только ждёт триггера. Сервер может вернуть одинаковый lifecycle
/// status в разных корзинах, поэтому восстанавливать готовность из одного
/// [IdeaState] небезопасно.
enum IdeaReadiness {
  waiting('Наблюдать', 'Ждём триггер'),
  active('Можно действовать', 'Триггер подтверждён');

  const IdeaReadiness(this.label, this.detail);

  final String label;
  final String detail;

  bool get canAct => this == IdeaReadiness.active;

  static IdeaReadiness? tryParse(String? raw) => switch (raw?.toUpperCase()) {
        'WAITING' || 'WATCH' || 'WAIT_FOR_TRIGGER' => IdeaReadiness.waiting,
        'ACTIVE' || 'TRADE_NOW' || 'ACTIONABLE' => IdeaReadiness.active,
        _ => null,
      };
}

/// Событийный риск идеи (ТЗ §9, §11.1).
class EventRisk {
  const EventRisk({
    required this.title,
    this.at,
    required this.impact,
    required this.policy,
    this.blocksEntry = false,
  });

  final String title;
  final DateTime? at;
  final EventImpact impact;

  /// Что политика делает с идеей: запрещает вход, режет риск, только метка.
  final String policy;

  /// Запрещает ли политика вход в окне события (ТЗ §11.1).
  final bool blocksEntry;

  Duration? until(DateTime now) => at?.difference(now);
}

/// Торговая идея — центральный объект ТЗ (§8, §9, §17).
///
/// Снимок неизменяем после перехода в Triggered (ТЗ §17.1): пересчёт с новой
/// логикой создаёт новую идею, а не переписывает старую. Поэтому переходы
/// возвращают новый объект, а не меняют поля на месте.
class Idea {
  const Idea({
    required this.id,
    required this.instrumentId,
    required this.instrumentName,
    required this.market,
    required this.direction,
    required this.strategy,
    required this.strategyVersion,
    required this.state,
    required this.score,
    required this.createdAt,
    required this.validUntil,
    required this.thesis,
    required this.plan,
    this.timeframes = const ['1D', '4H', '1H'],
    this.evidence = const [],
    this.annotations = const [],
    this.eventRisk,
    this.dataFlags = const [],
    this.skipReason,
    this.skipComment,
    this.riskChanged = false,
    this.stateChangedAt,
    this.sizingBlocker = '',
    this.closingReason = '',
    IdeaReadiness? readiness,
    bool? actionable,
  })  : _readiness = readiness,
        _actionable = actionable;

  final String id;
  final String instrumentId;
  final String instrumentName;
  final Market market;
  final Direction direction;
  final SetupStrategy strategy;
  final String strategyVersion;
  final IdeaState state;
  final QualityScore score;
  final DateTime createdAt;

  /// Срок жизни сигнала. После него состояние обязано стать Expired.
  final DateTime validUntil;

  /// Один-два абзаца: почему идея существует (ТЗ §9).
  final String thesis;

  /// План может отсутствовать у Watch: зона ещё не определена.
  final TradePlan? plan;

  final List<String> timeframes;
  final List<Evidence> evidence;
  final List<ChartAnnotation> annotations;
  final EventRisk? eventRisk;

  /// Флаги качества данных, на которых построена идея (приложение B).
  final List<DataQualityFlag> dataFlags;

  final SkipReason? skipReason;
  final String? skipComment;

  /// Для Active: риск-до-стопа изменился и требует внимания (ТЗ §6.2).
  final bool riskChanged;

  final DateTime? stateChangedAt;

  /// Почему движок считает объём неисполнимым. Пустая строка — исполним.
  ///
  /// Приезжает с сервера и повторяется дословно: причины разные («объём
  /// меньше лота», «курс USDT к рублю неизвестен», «объём посчитан до
  /// пересчёта валют»), и требуют они разного. Пересказ на клиенте склеил
  /// бы их в одно «нельзя».
  final String sizingBlocker;

  /// Почему идея закончилась — дословно из журнала переходов движка.
  ///
  /// Состояние отвечает «что», причина — «почему». «Рынок ушёл без нас» без
  /// пояснения не отличить от «срок истёк», а действия за ними разные:
  /// первое значит, что сетап отработал и входить уже некуда.
  final String closingReason;

  /// Серверная корзина готовности. У старых/локальных объектов поля нет —
  /// тогда сохраняем прежнее безопасное поведение и выводим готовность из
  /// lifecycle state. Для ответа сервера значение всегда задаётся явно.
  final IdeaReadiness? _readiness;
  final bool? _actionable;

  IdeaReadiness get readiness =>
      _readiness ?? (state.canConfirm ? IdeaReadiness.active : IdeaReadiness.waiting);

  /// Явное право показать действие. `WAITING` не становится исполнимой даже
  /// если её lifecycle status по ошибке выглядит как TRIGGERED.
  bool get actionable => _actionable ?? (readiness.canAct && state.canConfirm);

  /// Клиент может предложить серверную paper-сделку только при полном плане.
  /// Окончательная проверка всё равно выполняется сервером атомарно.
  bool get canApprovePaper =>
      readiness.canAct &&
      actionable &&
      state.canConfirm &&
      plan != null &&
      eventRisk?.blocksEntry != true &&
      sizingBlocker.isEmpty;

  /// Тикер для подписи: «CRYPTO:PERP:BTCUSDT» → «BTCUSDT».
  ///
  /// Целиком идентификатор в заголовок уведомления не помещается и
  /// обрезается на «CRYPTO:PER…» — по такой подписи нельзя понять, о каком
  /// активе пуш.
  String get symbolOrId {
    final cut = instrumentId.lastIndexOf(':');
    return cut < 0 ? instrumentId : instrumentId.substring(cut + 1);
  }

  /// Идея, которую нельзя показывать вовсе (ТЗ §14.2: ниже 65).
  bool get showable => score.value >= QualityScore.minimumToShow;

  bool isExpiredAt(DateTime now) => !now.isBefore(validUntil);

  /// Сколько осталось жить сигналу. Отрицательное — уже просрочен.
  Duration remaining(DateTime now) => validUntil.difference(now);

  /// Доля прожитого срока 0…1 — чем ближе к единице, тем срочнее.
  double ttlPressure(DateTime now) {
    final total = validUntil.difference(createdAt).inSeconds;
    if (total <= 0) return 1;
    final gone = now.difference(createdAt).inSeconds / total;
    return gone.clamp(0.0, 1.0);
  }

  /// Доказательства, конфликтующие между собой (ТЗ §9.1).
  ///
  /// Конфликт не скрывается и не усредняется: владелец видит обе стороны.
  List<Evidence> get conflicting =>
      evidence.where((e) => e.hasConflict).toList();

  /// Метки только тех слоёв, которые участвовали в оценке (ТЗ §9.1).
  ///
  /// Рисовать Wyckoff, если детектор не дал доказательства, запрещено:
  /// картинка обязана повторять текст, а не украшать его.
  /// Метки видимых слоёв.
  ///
  /// Отбор «только то, что участвовало в оценке» делает **движок**: он
  /// строит разметку исключительно из показаний, на которых стратегия
  /// собрала план (`candidate.used`). Второй такой же фильтр здесь был не
  /// страховкой, а поломкой: уровни плана приходят со ссылкой `plan`, для
  /// которой отдельного доказательства не существует, — и вход, стоп и цели
  /// не рисовались никогда. А пока лента отдаёт сводку без доказательств,
  /// этот фильтр гасил и Вайкоффа, и SMC, и трендовые: график оставался
  /// голыми свечами.
  List<ChartAnnotation> annotationsFor(Set<ChartLayer> visible) =>
      _allAnnotations.where((a) => visible.contains(a.layer)).toList()
        ..sort((a, b) => b.displayPriority.compareTo(a.displayPriority));

  /// Слои, которые вообще имеет смысл включать для этой идеи.
  Set<ChartLayer> get availableLayers => {
        ChartLayer.candles,
        for (final a in _allAnnotations) a.layer,
      };

  /// Разметка идеи вместе с уровнями плана.
  ///
  /// Уровни плана строит движок и кладёт в снимок идеи. Но идея — снимок
  /// неизменяемый: та, что посчитана вчерашней версией движка, живёт свой
  /// срок как есть, и переписать её задним числом нельзя — по ней принято
  /// решение. Владельцу же нужен график **сейчас**, а не через три дня.
  ///
  /// Поэтому недостающие уровни достраиваются здесь из плана. Это не
  /// выдуманные данные: те же вход, стоп и цели уже показаны текстом рядом,
  /// и §9.1 требует ровно того, чтобы картинка повторяла текст. Находки
  /// детекторов (Вайкофф, смарт-мани) так восстановить нельзя — их здесь и
  /// не появляется, и это честно видно по отсутствию их слоёв.
  List<ChartAnnotation> get _allAnnotations {
    final present = {for (final a in annotations) a.type};
    final plan = this.plan;
    if (plan == null) return annotations;
    final extra = <ChartAnnotation>[
      // Момент рождения идеи. Первое, о чём спрашивают, глядя на график:
      // на какой свече это появилось. Уровни плана тянутся от края до края
      // и сами по себе не отделяют «до» от «после» — а именно по этому
      // видно, отработал сетап или ещё нет.
      if (!present.contains(AnnotationType.planCreated))
        ChartAnnotation(
          id: 'plan-created',
          type: AnnotationType.planCreated,
          timeframe: timeframes.isEmpty ? '' : timeframes.last,
          startTime: createdAt,
          endTime: createdAt,
          confidence: 1,
          evidenceId: 'plan',
          detectorVersion: 'plan-local',
          label: 'Идея',
          // Ниже уровней плана: вертикаль момента и горизонтали цен не
          // спорят за одни пиксели, а стоп обязан остаться самым верхним
          // объектом разметки.
          displayPriority: 70,
        ),
      if (!present.contains(AnnotationType.levelEntry))
        ChartAnnotation(
          id: 'plan-entry',
          type: AnnotationType.levelEntry,
          timeframe: timeframes.isEmpty ? '' : timeframes.last,
          startTime: createdAt,
          endTime: validUntil,
          confidence: 1,
          evidenceId: 'plan',
          detectorVersion: 'plan-local',
          label: 'Зона входа',
          priceLow: plan.entryLow < plan.entryHigh ? plan.entryLow : plan.entryHigh,
          priceHigh: plan.entryLow < plan.entryHigh ? plan.entryHigh : plan.entryLow,
          displayPriority: 95,
        ),
      if (!present.contains(AnnotationType.levelStop))
        ChartAnnotation(
          id: 'plan-stop',
          type: AnnotationType.levelStop,
          timeframe: timeframes.isEmpty ? '' : timeframes.last,
          startTime: createdAt,
          endTime: validUntil,
          confidence: 1,
          evidenceId: 'plan',
          detectorVersion: 'plan-local',
          label: 'Стоп',
          priceLow: plan.stop,
          priceHigh: plan.stop,
          displayPriority: 96,
        ),
      if (!present.contains(AnnotationType.levelTarget))
        for (var i = 0; i < plan.targets.length; i++)
          ChartAnnotation(
            id: 'plan-tp${i + 1}',
            type: AnnotationType.levelTarget,
            timeframe: timeframes.isEmpty ? '' : timeframes.last,
            startTime: createdAt,
            endTime: validUntil,
            confidence: 1,
            evidenceId: 'plan',
            detectorVersion: 'plan-local',
            label: 'TP${i + 1}',
            priceLow: plan.targets[i].price,
            priceHigh: plan.targets[i].price,
            displayPriority: 80,
          ),
    ];
    return extra.isEmpty ? annotations : [...annotations, ...extra];
  }

  /// Переход состояния с проверкой машины (ТЗ §8).
  ///
  /// Недопустимый переход — это ошибка логики, а не пользовательский ввод:
  /// молча его проглотить значит спрятать баг до момента с деньгами.
  Idea moveTo(IdeaState next, {required DateTime at, SkipReason? reason, String? comment}) {
    if (!state.canMoveTo(next)) {
      throw StateError('переход ${state.name} → ${next.name} запрещён (ТЗ §8)');
    }
    if (next == IdeaState.skipped && reason == null) {
      throw StateError('пропуск без причины не сохраняется (ТЗ §12)');
    }
    if (reason != null && reason.needsComment && (comment == null || comment.trim().isEmpty)) {
      throw StateError('причина «${reason.label}» требует комментария');
    }
    return copyWith(
      state: next,
      stateChangedAt: at,
      skipReason: reason ?? skipReason,
      skipComment: comment ?? skipComment,
    );
  }

  Idea copyWith({
    IdeaState? state,
    QualityScore? score,
    TradePlan? plan,
    DateTime? stateChangedAt,
    SkipReason? skipReason,
    String? skipComment,
    bool? riskChanged,
    IdeaReadiness? readiness,
    bool? actionable,
  }) =>
      Idea(
        id: id,
        instrumentId: instrumentId,
        instrumentName: instrumentName,
        market: market,
        direction: direction,
        strategy: strategy,
        strategyVersion: strategyVersion,
        state: state ?? this.state,
        score: score ?? this.score,
        createdAt: createdAt,
        validUntil: validUntil,
        thesis: thesis,
        plan: plan ?? this.plan,
        timeframes: timeframes,
        evidence: evidence,
        annotations: annotations,
        eventRisk: eventRisk,
        dataFlags: dataFlags,
        skipReason: skipReason ?? this.skipReason,
        skipComment: skipComment ?? this.skipComment,
        riskChanged: riskChanged ?? this.riskChanged,
        stateChangedAt: stateChangedAt ?? this.stateChangedAt,
        sizingBlocker: sizingBlocker,
        closingReason: closingReason,
        readiness: readiness ?? _readiness,
        actionable: actionable ?? _actionable,
      );
}

/// Приоритет идеи на экране Today (ТЗ §6.2).
///
/// Формула задана ТЗ:
/// `priority = 0.35*urgency + 0.30*quality + 0.20*decision_value + 0.15*risk_impact`.
/// Сами слагаемые ТЗ не определяет — это наша трактовка, и она здесь, в
/// одном месте, а не размазана по экранам:
///
/// * **urgency** — состояние плюс давление срока: Triggered всегда срочнее
///   Ready, но внутри состояния истекающий сигнал обгоняет свежий.
/// * **decision_value** — что меняется от решения прямо сейчас.
/// * **risk_impact** — какую долю свободного лимита займёт идея.
///
/// Рядом с формулой ТЗ §6.2 стоит вторая строка — «Triggered > Ready >
/// Active-with-risk-change > Watch». Одна формула этого не гарантирует:
/// Watch с оценкой 100 обгонит Triggered с оценкой 70. Поэтому порядок
/// двухуровневый — сначала очередь состояния, внутри неё формула. Иначе
/// истекающий сигнал вытеснялся бы с экрана наблюдением.
abstract final class IdeaPriority {
  static const wUrgency = 0.35;
  static const wQuality = 0.30;
  static const wDecision = 0.20;
  static const wRiskImpact = 0.15;

  /// Очередь состояния (ТЗ §6.2). Больше — выше.
  static int tier(Idea idea) => switch (idea.state) {
        IdeaState.triggered =>
          idea.readiness.canAct && idea.actionable ? 4 : 1,
        IdeaState.ready => 3,
        IdeaState.active => idea.riskChanged ? 2 : 1,
        IdeaState.watch => 1,
        _ => 0,
      };

  /// Базовая срочность состояния.
  static double stateUrgency(IdeaState state, {bool riskChanged = false}) =>
      switch (state) {
        IdeaState.triggered => 0.8,
        IdeaState.ready => 0.6,
        IdeaState.active => riskChanged ? 0.4 : 0.2,
        IdeaState.watch => 0.0,
        _ => 0.0,
      };

  static double urgency(Idea idea, DateTime now) {
    final base = stateUrgency(idea.state, riskChanged: idea.riskChanged);
    // Давление срока имеет смысл только для живого сигнала: у Active срок
    // определяется рынком, а не таймером идеи.
    final pressure = idea.state.needsAttention ? idea.ttlPressure(now) : 0.0;
    return (base + 0.15 * pressure).clamp(0.0, 1.0);
  }

  static double decisionValue(Idea idea) => switch (idea.state) {
        IdeaState.triggered => 1.0,
        IdeaState.ready => 0.6,
        IdeaState.active => idea.riskChanged ? 0.8 : 0.3,
        IdeaState.watch => 0.1,
        _ => 0.0,
      };

  /// Доля открытого лимита риска, которую займёт идея (ТЗ §20: cap 2%).
  static double riskImpact(Idea idea) {
    final risk = idea.plan?.riskPercent ?? idea.score.riskPercent;
    if (risk == null || risk <= 0) return 0;
    return (risk / RiskBudget.openRiskPercent).clamp(0.0, 1.0);
  }

  /// Итоговый приоритет 0…1.
  static double of(Idea idea, DateTime now) =>
      wUrgency * urgency(idea, now) +
      wQuality * (idea.score.value / 100) +
      wDecision * decisionValue(idea) +
      wRiskImpact * riskImpact(idea);

  /// Максимум карточек на Today (ТЗ §6.2). Остальное — в разделе «Идеи».
  static const todayLimit = 3;

  /// Отсортированный список идей: сначала очередь состояния, внутри неё —
  /// формула ТЗ §6.2.
  ///
  /// Терминальные идеи в ленту решений не попадают: решать по ним нечего.
  /// Идеи ниже порога показа отсеиваются здесь же (ТЗ §14.2).
  static List<Idea> rank(List<Idea> ideas, DateTime now) {
    final live = ideas.where((i) => !i.state.isTerminal && i.showable).toList();
    live.sort((a, b) {
      final byTier = tier(b).compareTo(tier(a));
      if (byTier != 0) return byTier;
      final byPriority = of(b, now).compareTo(of(a, now));
      if (byPriority != 0) return byPriority;
      // При равенстве вперёд идёт та, у которой меньше времени осталось.
      return a.validUntil.compareTo(b.validUntil);
    });
    return live;
  }

  /// Топ для экрана Today — не более трёх.
  static List<Idea> top(List<Idea> ideas, DateTime now) =>
      rank(ideas, now).take(todayLimit).toList();
}
