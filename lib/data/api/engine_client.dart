import '../../domain/models/signal.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/paper_position.dart';
import '../../domain/portfolio/package.dart';
import '../../domain/research/hypothesis.dart';
import 'api_client.dart';
import 'engine_contract.dart';

/// Клиент движка по контракту §18.
///
/// Отдельный от [ApiClient] слой существует ровно затем, чтобы разбор
/// контракта был в одном месте и проверялся тестами. Экраны получают готовые
/// доменные объекты и ничего не знают ни о путях, ни о форме JSON.
///
/// Отказ здесь — **состояние**, а не исключение, которое кто-то проглотит.
/// Пустой список идей и недоступный сервер выглядят на экране одинаково, если
/// разницу не передать явно; ТЗ §24 прямо требует показывать деградацию, а не
/// маскировать её под «сегодня сетапов нет».
class EngineIdeas {
  const EngineIdeas({
    required this.ideas,
    this.unavailableReason,
    this.noSetupsReason,
  });

  const EngineIdeas.unavailable(String reason)
      : ideas = const [],
        unavailableReason = reason,
        noSetupsReason = null;

  final List<Idea> ideas;

  /// Почему движок недоступен. null — движок ответил.
  final String? unavailableReason;

  /// Почему сетапов нет, если движок ответил пустым списком.
  final String? noSetupsReason;

  bool get isAvailable => unavailableReason == null;
}

/// Результат решения владельца по серверной идее.
///
/// Сервер возвращает envelope и может приложить созданную paper-сделку.
/// Поля envelope сохраняются, чтобы повторный идемпотентный ответ не
/// выглядел как новая сделка.
class IdeaDecision {
  const IdeaDecision({
    required this.ideaId,
    required this.decision,
    required this.ideaStatus,
    required this.paperOnly,
    required this.idempotentReplay,
    this.trade,
  });

  final String ideaId;
  final String decision;
  final String ideaStatus;
  final bool paperOnly;
  final bool idempotentReplay;
  final PaperPosition? trade;

  factory IdeaDecision.fromJson(Map<String, dynamic> json) {
    // Отсутствующий признак нельзя трактовать как paper-only. Иначе ответ
    // неизвестного/старого live-контракта выглядел бы безопасным и controller
    // обновил бы локальное состояние до проверки режима исполнения.
    if (json['paper_only'] != true) {
      throw ApiException(
        'Сервер не подтвердил безопасный режим paper-only. Решение не принято.',
      );
    }
    final decision = json['decision'];
    if (decision is! String || decision.isEmpty) {
      throw ApiException('Сервер вернул решение без типа. Решение не принято.');
    }
    return IdeaDecision(
      ideaId: '${json['idea_id'] ?? _nestedIdeaId(json) ?? ''}',
      decision: decision,
      ideaStatus: '${json['idea_status'] ?? ''}',
      paperOnly: true,
      idempotentReplay: json['idempotent_replay'] == true,
      trade: EngineContract.paperTrade(json),
    );
  }

  static Object? _nestedIdeaId(Map<String, dynamic> json) {
    final trade = json['trade'];
    return trade is Map<String, dynamic> ? trade['idea_id'] : null;
  }
}

/// Exact server-owned economics shown to the owner before a manual risk boost.
///
/// Money deliberately stays as strings. The phone must not recalculate risk,
/// quantity, leverage or liquidation economics from binary floating point and
/// accidentally confirm numbers different from the signed server preview.
class RiskPreview {
  const RiskPreview({
    required this.ideaId,
    required this.riskSnapshotId,
    required this.presetId,
    required this.executionMode,
    required this.allowed,
    required this.warnings,
    required this.blockers,
    required this.autoRiskPct,
    required this.autoRiskAmount,
    required this.requestedRiskPct,
    required this.requestedRiskAmount,
    required this.effectiveRiskPct,
    required this.effectiveRiskAmount,
    required this.hardCapRiskPct,
    required this.quantity,
    required this.notional,
    required this.resultingLeverage,
    required this.liquidationDistanceRatio,
    required this.totalOpenRiskAfter,
    required this.clusterRiskAfter,
    required this.worstCaseStopLoss,
    required this.bindingConstraint,
    required this.issuedAt,
    required this.expiresAt,
    required this.previewHash,
  });

  final String ideaId;
  final String riskSnapshotId;
  final String presetId;
  final String executionMode;
  final bool allowed;
  final List<String> warnings;
  final List<String> blockers;
  final String autoRiskPct;
  final String autoRiskAmount;
  final String requestedRiskPct;
  final String requestedRiskAmount;
  final String effectiveRiskPct;
  final String effectiveRiskAmount;
  final String hardCapRiskPct;
  final String quantity;
  final String notional;
  final String? resultingLeverage;
  final String? liquidationDistanceRatio;
  final String totalOpenRiskAfter;
  final String clusterRiskAfter;
  final String worstCaseStopLoss;
  final String bindingConstraint;
  final DateTime issuedAt;
  final DateTime expiresAt;

  /// Replayable signed token. Never persist it or place it in logs/keys.
  final String previewHash;

  bool get canConfirm => allowed && previewHash.isNotEmpty;
  bool get isExpired => !DateTime.now().toUtc().isBefore(expiresAt.toUtc());

  factory RiskPreview.fromJson(Map<String, dynamic> json) {
    String requiredString(String key) {
      final value = json[key];
      if (value is! String || value.isEmpty) {
        throw ApiException('Сервер вернул preview без обязательного поля $key.');
      }
      return value;
    }

    String? optionalString(String key) {
      final value = json[key];
      if (value == null) return null;
      if (value is! String || value.isEmpty) {
        throw ApiException('Сервер вернул некорректное поле $key в preview.');
      }
      return value;
    }

    List<String> stringList(String key) {
      final raw = json[key];
      if (raw is! List || raw.any((item) => item is! String)) {
        throw ApiException('Сервер вернул некорректный список $key в preview.');
      }
      return List<String>.unmodifiable(raw.cast<String>());
    }

    final allowed = json['allowed'];
    if (allowed is! bool) {
      throw ApiException('Сервер не указал, разрешён ли manual-risk preview.');
    }
    final issuedAt = DateTime.tryParse(requiredString('issued_at'));
    final expiresAt = DateTime.tryParse(requiredString('expires_at'));
    if (issuedAt == null || expiresAt == null || !expiresAt.isAfter(issuedAt)) {
      throw ApiException('Сервер вернул некорректный срок действия risk preview.');
    }
    final previewHash = json['preview_hash'];
    if (previewHash is! String || (allowed && previewHash.isEmpty)) {
      throw ApiException('Разрешённый risk preview не подписан сервером.');
    }
    return RiskPreview(
      ideaId: requiredString('idea_id'),
      riskSnapshotId: requiredString('risk_snapshot_id'),
      presetId: requiredString('preset_id'),
      executionMode: requiredString('execution_mode'),
      allowed: allowed,
      warnings: stringList('warnings'),
      blockers: stringList('blockers'),
      autoRiskPct: requiredString('auto_risk_pct'),
      autoRiskAmount: requiredString('auto_risk_amount'),
      requestedRiskPct: requiredString('requested_risk_pct'),
      requestedRiskAmount: requiredString('requested_risk_amount'),
      effectiveRiskPct: requiredString('effective_risk_pct'),
      effectiveRiskAmount: requiredString('effective_risk_amount'),
      hardCapRiskPct: requiredString('hard_cap_risk_pct'),
      quantity: requiredString('quantity'),
      notional: requiredString('notional'),
      resultingLeverage: optionalString('resulting_leverage'),
      liquidationDistanceRatio: optionalString('liquidation_distance_ratio'),
      totalOpenRiskAfter: requiredString('total_open_risk_after'),
      clusterRiskAfter: requiredString('cluster_risk_after'),
      worstCaseStopLoss: requiredString('worst_case_stop_loss'),
      bindingConstraint: requiredString('binding_constraint'),
      issuedAt: issuedAt,
      expiresAt: expiresAt,
      previewHash: previewHash,
    );
  }
}

/// Durable result of applying the exact signed preview after a fresh recheck.
class RiskOverrideResult {
  const RiskOverrideResult({
    required this.overrideId,
    required this.ideaId,
    required this.riskSnapshotId,
    required this.presetId,
    required this.executionMode,
    required this.venue,
    required this.account,
    required this.effectiveRiskPct,
    required this.effectiveQuantity,
    required this.effectiveLeverage,
    required this.created,
  });

  final String overrideId;
  final String ideaId;
  final String riskSnapshotId;
  final String presetId;
  final String executionMode;
  final String venue;
  final String account;
  final String effectiveRiskPct;
  final String effectiveQuantity;
  final String? effectiveLeverage;
  final bool created;

  factory RiskOverrideResult.fromJson(Map<String, dynamic> json) {
    String requiredString(String key) {
      final value = json[key];
      if (value is! String || value.isEmpty) {
        throw ApiException('Сервер вернул risk override без поля $key.');
      }
      return value;
    }

    String? optionalString(String key) {
      final value = json[key];
      if (value == null) return null;
      if (value is! String || value.isEmpty) {
        throw ApiException('Сервер вернул некорректное поле $key в override.');
      }
      return value;
    }

    final created = json['created'];
    if (created is! bool) {
      throw ApiException('Сервер не указал, создан ли новый risk override.');
    }
    return RiskOverrideResult(
      overrideId: requiredString('override_id'),
      ideaId: requiredString('idea_id'),
      riskSnapshotId: requiredString('risk_snapshot_id'),
      presetId: requiredString('preset_id'),
      executionMode: requiredString('execution_mode'),
      venue: requiredString('venue'),
      account: requiredString('account'),
      effectiveRiskPct: requiredString('effective_risk_pct'),
      effectiveQuantity: requiredString('effective_quantity'),
      effectiveLeverage: optionalString('effective_leverage'),
      created: created,
    );
  }
}

enum EngineFailureStage {
  ideaHydration,
  chartLoad,
  sandboxReconciliation,
}

class EngineHandledFailure {
  const EngineHandledFailure({
    required this.stage,
    required this.error,
    this.stackTrace,
  });

  final EngineFailureStage stage;
  final Object error;
  final StackTrace? stackTrace;
}

typedef EngineFailureReporter = void Function(EngineHandledFailure failure);

class EngineClient {
  EngineClient({
    ApiClient? client,
    EngineFailureReporter? onHandledFailure,
  })  : _api = client ?? ApiClient(),
        _onHandledFailure = onHandledFailure;

  final ApiClient _api;
  EngineFailureReporter? _onHandledFailure;

  static const _base = '/api/v1';

  /// Allows the app bootstrap to attach one shared local diagnostic recorder
  /// after a specialized EngineClient subclass has been constructed.
  void setHandledFailureReporter(EngineFailureReporter? reporter) {
    _onHandledFailure = reporter;
  }

  /// Reports a failure that the UX intentionally handles without throwing.
  /// Subclasses use the same reporter so one device-local recorder owns all
  /// diagnostics and applies the same secret redaction before persistence.
  void reportHandledFailure(
    EngineFailureStage stage,
    Object error, [
    StackTrace? stackTrace,
  ]) {
    try {
      _onHandledFailure?.call(
        EngineHandledFailure(
          stage: stage,
          error: error,
          stackTrace: stackTrace,
        ),
      );
    } on Object {
      // Diagnostics must never turn an intentionally handled failure fatal.
    }
  }

  /// Адреса движка нет — и это чинится в приложении, без пересборки.
  ///
  /// Прежний текст сообщал диагноз («не задан в сборке») и молчал о
  /// лечении. Владелец при этом упирался в пустой экран, хотя поле адреса
  /// лежит через два касания. Причина без выхода из неё — половина ответа.
  static const _noAddress =
      'Адрес движка не задан. Идеи и пакеты считает сервер — задайте его '
      'адрес в «Настройках» → «Подключения» → «Задать адрес».';

  bool get isConfigured => _api.baseUrl.isNotEmpty;

  /// Карточки дня (§16): не больше трёх, с причиной, если торговать нечего.
  Future<EngineIdeas> today() async {
    if (!isConfigured) {
      return const EngineIdeas.unavailable(_noAddress);
    }
    try {
      final json = await _api.get('$_base/ideas/today');
      // Два списка, а не один: §16 разделяет «торговать сейчас» и «ждать
      // триггера». Слить их значило бы выдать наблюдение за готовую сделку.
      final ideas = <Idea>[
        ..._parse(
          json['trade_now'],
          readiness: IdeaReadiness.active,
        ),
        ..._parse(
          json['wait_for_trigger'],
          readiness: IdeaReadiness.waiting,
          actionable: false,
        ),
      ];
      return EngineIdeas(
        ideas: ideas,
        noSetupsReason: ideas.isEmpty
            ? (json['no_trade_reason'] as String? ??
                'Движок отработал и не нашёл сетапов, проходящих допуск §16.')
            : null,
      );
    } catch (error) {
      return EngineIdeas.unavailable(_reason(error));
    }
  }

  static List<Idea> _parse(
    Object? raw, {
    IdeaReadiness? readiness,
    bool? actionable,
  }) {
    if (raw is! List) return const [];
    return [
      for (final item in raw)
        if (item is Map<String, dynamic>)
          EngineContract.idea(
            item,
            readiness: readiness,
            actionable: actionable,
          ),
    ];
  }

  /// Полная карточка с планом, разбором оценки, доказательствами и разметкой.
  Future<Idea?> detail(String id) async {
    if (!isConfigured) return null;
    try {
      final json = await _api.get('$_base/ideas/$id');
      return EngineContract.idea(json);
    } catch (error, stackTrace) {
      // Сводка из ленты уже на экране; упавшая догрузка полной карточки не
      // должна ронять разбор — просто останется меньше подробностей. Но
      // диагностический след теперь остаётся и переживает перезапуск.
      reportHandledFailure(EngineFailureStage.ideaHydration, error, stackTrace);
      return null;
    }
  }

  /// Лента идей, включая непоказанные (§12): журнал обязан отвечать на
  /// вопрос, что система нашла, но не показала.
  Future<EngineIdeas> feed({int limit = 50}) async {
    if (!isConfigured) {
      return const EngineIdeas.unavailable(_noAddress);
    }
    try {
      // Лента отдаётся массивом верхнего уровня — оборачиваем его на
      // стороне клиента, чтобы разбор был один и тот же для обеих форм.
      return EngineIdeas(
        ideas: _parse(await _api.getList('$_base/ideas?limit=$limit')),
      );
    } catch (error) {
      return EngineIdeas.unavailable(_reason(error));
    }
  }

  /// Свечи инструмента (§23, `GET /api/v1/market/{id}/bars`).
  ///
  /// До этого график идеи не наполнялся ничем: свечи умел строить только
  /// скринер на устройстве, а идеи приходят с движка — и разбор всегда
  /// показывал заглушку «живой график недоступен». Два конца одной картинки
  /// были подключены к разным источникам.
  ///
  /// Цены приходят строками намеренно (`Money` в схемах сервера): JSON-число
  /// это double, и шаг цены 0,005 на другом конце становится
  /// 0,004999999999999999. Разбираем строку, а не число.
  ///
  /// `closed_only` не трогаем — сервер по умолчанию отдаёт только закрытые
  /// бары (§4.4). Незакрытый бар на графике живёт своей жизнью между двумя
  /// запросами, и разметка по нему уезжает.
  Future<SignalChart?> bars(
    String instrumentId, {
    required String timeframe,
    int limit = 200,
    void Function(String reason)? onFailure,
  }) async {
    if (!isConfigured) {
      onFailure?.call('адрес движка не задан');
      return null;
    }
    try {
      final raw = await _api.getList(
        '$_base/market/$instrumentId/bars?timeframe=$timeframe&limit=$limit',
      );
      final candles = <ChartCandle>[];
      for (final item in raw) {
        if (item is! Map<String, dynamic>) continue;
        final open = _price(item['open']);
        final high = _price(item['high']);
        final low = _price(item['low']);
        final close = _price(item['close']);
        // Свеча без полной цены не рисуется вовсе: дорисованный нулём бар
        // выглядит как обвал, которого не было.
        if (open == null || high == null || low == null || close == null) {
          continue;
        }
        candles.add(ChartCandle(
          open,
          high,
          low,
          close,
          DateTime.tryParse('${item['open_time']}')?.toLocal(),
        ));
      }
      if (candles.isEmpty) {
        onFailure?.call('у движка нет свечей $timeframe по этому инструменту');
        return null;
      }
      return SignalChart(timeframeLabel: timeframe, candles: candles);
    } catch (error) {
      // График — не то, ради чего стоит рушить разбор идеи. Его отсутствие
      // видно на самом графике, и там же теперь написана причина: раньше
      // перехват был нем, и заглушка ничего не объясняла.
      onFailure?.call('движок: ${_reason(error)}');
      return null;
    }
  }

  /// Каноническая последовательность таймфреймов для графика.
  ///
  /// Detail может назвать один и тот же период как `H4` или `4H`, а bars
  /// принимает канонические `4h`, `1h`, `1d`. Алиасы нормализуются до
  /// запроса, затем пробуются сетапный, 4H, 1H и D1 без дублей.
  static List<String> chartTimeframes(String setup) {
    String canonical(String raw) => switch (raw.trim().toLowerCase()) {
          'h4' || '4h' => '4h',
          'h1' || '1h' => '1h',
          'd1' || '1d' => '1d',
          final value when value.isNotEmpty => value,
          _ => '4h',
        };

    final result = <String>[];
    for (final value in [canonical(setup), '4h', '1h', '1d']) {
      if (!result.contains(value)) result.add(value);
    }
    return result;
  }

  /// Свечи с детерминированной деградацией таймфрейма.
  ///
  /// Промежуточные 404/пустые ряды не показываются пользователю как итоговая
  /// ошибка. Причина сообщается только после исчерпания всей цепочки.
  Future<SignalChart?> barsWithFallback(
    String instrumentId, {
    required String setupTimeframe,
    int limit = 200,
    void Function(String reason)? onFailure,
  }) async {
    final attempts = chartTimeframes(setupTimeframe);
    final reasons = <String>[];
    for (final timeframe in attempts) {
      final chart = await bars(
        instrumentId,
        timeframe: timeframe,
        limit: limit,
        onFailure: (reason) => reasons.add('$timeframe: $reason'),
      );
      if (chart != null) return chart;
    }
    final details = reasons.isEmpty ? '' : ' ${reasons.join(' · ')}';
    final reason =
        'Свечи недоступны: проверены ${attempts.join(', ')}.$details';
    onFailure?.call(reason);
    // Не записываем instrumentId: для диагностики достаточно стадии,
    // таймфреймов и сетевой причины, а идентификатор инструмента здесь не
    // нужен и раздувал бы локальную телеметрию.
    reportHandledFailure(
      EngineFailureStage.chartLoad,
      StateError(reason),
      StackTrace.current,
    );
    return null;
  }

  /// Явно принять только paper-сделку. Повтор безопасен по idea id.
  Future<IdeaDecision> approvePaper(String ideaId) async {
    if (!isConfigured) throw ApiException(_noAddress);
    final json = await _api.post(
      '$_base/ideas/$ideaId/approve-paper',
      idempotencyKey: 'approve-paper:$ideaId',
    );
    final result = IdeaDecision.fromJson(json);
    if (result.decision != 'APPROVED_PAPER' ||
        result.ideaId != ideaId ||
        result.trade == null ||
        result.trade!.ideaId != ideaId) {
      throw ApiException(
        'Сервер не подтвердил создание paper-сделки для этой идеи.',
      );
    }
    return result;
  }

  /// Отклонить серверную идею с причиной из общего справочника.
  Future<IdeaDecision> rejectIdea(
    String ideaId, {
    required String reason,
    String comment = '',
  }) async {
    if (!isConfigured) throw ApiException(_noAddress);
    final json = await _api.post(
      '$_base/ideas/$ideaId/reject',
      body: {'reason': reason, 'comment': comment},
      idempotencyKey: 'reject:$ideaId',
    );
    final result = IdeaDecision.fromJson(json);
    if (result.decision != 'REJECTED' || result.ideaId != ideaId) {
      throw ApiException('Сервер не подтвердил отказ от этой идеи.');
    }
    return result;
  }

  /// Ask the server for one bounded named manual-risk preset.
  ///
  /// The mobile request carries no money-bearing values. `currentMode` is the
  /// mode the phone last observed from the server and is only a stale-state
  /// guard; the server re-reads its own current mode before calculating.
  Future<RiskPreview> previewRisk({
    required String ideaId,
    required String presetId,
    required String currentMode,
  }) async {
    if (!isConfigured) throw ApiException(_noAddress);
    final idea = ideaId.trim();
    final preset = presetId.trim();
    final mode = currentMode.trim();
    if (idea.isEmpty || preset.isEmpty || mode.isEmpty) {
      throw ApiException('Для risk preview нужны идея, preset и режим сервера.');
    }
    final json = await _api.post(
      '$_base/risk/preview',
      body: {
        'idea_id': idea,
        'preset_id': preset,
        'current_mode': mode,
      },
    );
    final preview = RiskPreview.fromJson(json);
    if (preview.ideaId != idea ||
        preview.presetId != preset ||
        preview.executionMode != mode) {
      throw ApiException(
        'Сервер вернул risk preview для другой идеи, preset или режима.',
      );
    }
    return preview;
  }

  /// Confirm exactly the signed preview after the server performs a fresh
  /// recheck. The request intentionally contains no client-authored economics.
  Future<RiskOverrideResult> applyRisk({
    required RiskPreview preview,
    required String reason,
  }) async {
    if (!isConfigured) throw ApiException(_noAddress);
    if (!preview.canConfirm) {
      throw ApiException('Этот risk preview нельзя подтвердить.');
    }
    final why = reason.trim();
    if (why.isEmpty) {
      throw ApiException('Для ручного повышения риска нужна причина.');
    }
    // Stable for this exact server preview without embedding the replayable
    // signed token. Network retries therefore reuse the same key while logs,
    // proxies and crash reports never learn the token itself.
    final idempotencyKey = 'manual-risk:${preview.ideaId}:${preview.presetId}:'
        '${preview.issuedAt.toUtc().microsecondsSinceEpoch}';
    final json = await _api.post(
      '$_base/risk/override',
      body: {
        'idea_id': preview.ideaId,
        'preset_id': preview.presetId,
        'current_mode': preview.executionMode,
        'preview_hash': preview.previewHash,
        'owner_confirmed': true,
        'reason': why,
      },
      idempotencyKey: idempotencyKey,
    );
    final result = RiskOverrideResult.fromJson(json);
    if (result.ideaId != preview.ideaId ||
        result.riskSnapshotId != preview.riskSnapshotId ||
        result.presetId != preview.presetId ||
        result.executionMode != preview.executionMode ||
        result.effectiveRiskPct != preview.effectiveRiskPct ||
        result.effectiveQuantity != preview.quantity ||
        result.effectiveLeverage != preview.resultingLeverage) {
      throw ApiException(
        'Сервер подтвердил risk override, не совпадающий с показанным preview.',
      );
    }
    return result;
  }

  /// Пакеты капитала (§6, `GET /api/v1/portfolio/packages`).
  ///
  /// Ответ несёт и состав, и состояние конвейера. Второе не менее важно:
  /// пустой список пакетов сам по себе не отвечает на вопрос «почему», а
  /// именно его владелец и задаёт, открывая пустой экран.
  Future<PortfolioState> portfolio() async {
    if (!isConfigured) {
      return const PortfolioState.unavailable(_noAddress);
    }
    try {
      return EngineContract.portfolio(
          await _api.get('$_base/portfolio/packages'));
    } catch (error) {
      return PortfolioState.unavailable(_reason(error));
    }
  }

  /// Сверить текущий инвестиционный счёт с выбранным пакетом.
  ///
  /// GET остаётся advisory-only: клиент не получает ни order id, ни флага
  /// исполнения и не может превратить ответ в заявку.
  Future<PortfolioRebalance> portfolioRebalance(String modelId) async {
    final selected = modelId.trim();
    if (selected.isEmpty) {
      return const PortfolioRebalance.unavailable('Пакет не выбран.');
    }
    if (!isConfigured) {
      return const PortfolioRebalance.unavailable(_noAddress);
    }
    try {
      final raw = await _api.get(
        '$_base/portfolio/rebalance?model_id=${Uri.encodeQueryComponent(selected)}',
      );
      final result = EngineContract.portfolioRebalance(raw);
      if (result.modelId.isNotEmpty && result.modelId != selected) {
        return const PortfolioRebalance.unavailable(
          'Движок вернул ребаланс другого пакета. Обновите пакеты и повторите.',
        );
      }
      return result;
    } catch (error) {
      return PortfolioRebalance.unavailable(_reason(error));
    }
  }

  /// Ранние сигналы: гипотезы и готовность источников.
  ///
  /// Отдельно от идей и от пакетов, потому что это третий вопрос. Идеи —
  /// когда входить, пакеты — как разложить капитал, гипотезы — что
  /// изменилось в экономике эмитента и стоит ли это изучать. Исполнять
  /// гипотезу нечем, и подтверждать кнопкой тоже.
  Future<ResearchState> research() async {
    if (!isConfigured) {
      return const ResearchState.unavailable(_noAddress);
    }
    try {
      return ResearchState.fromJson(
        await _api.get('$_base/research/hypotheses'),
      );
    } catch (error) {
      return ResearchState.unavailable(_reason(error));
    }
  }

  /// Отправить движку снимок позиций инвестиционного счёта.
  ///
  /// Счёт читает устройство, а не сервер, и это не разделение труда, а
  /// граница безопасности. Токен Т-Инвестиций на чтение лежит в защищённом
  /// хранилище телефона; он привязан к пользователю, а не к счёту, и видит
  /// **все** счета владельца. Класть такой токен на VPS значило бы сложить
  /// весь капитал в одну точку отказа ради удобства синхронизации.
  ///
  /// Сервер по снимку считает расхождение с целевым составом и предлагает,
  /// что поправить. Заявок по этому предложению не отправляет никто.
  ///
  /// Возвращает ответ движка или null, если отправить не удалось. Отказ —
  /// не исключение: снимок посылается фоном, и ронять из-за него чтение
  /// счёта нельзя.
  Future<Map<String, dynamic>?> putHoldings({
    required String accountId,
    required List<Map<String, dynamic>> positions,
    String broker = 'tinvest',
    String title = '',
    String? equity,
  }) async {
    if (!isConfigured || accountId.isEmpty) return null;
    try {
      return await _api.post('$_base/portfolio/holdings', body: {
        'broker': broker,
        'account_id': accountId,
        if (title.isNotEmpty) 'title': title,
        'equity': ?equity,
        'positions': positions,
      });
    } catch (_) {
      return null;
    }
  }

  static double? _price(Object? raw) => switch (raw) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  /// Живые бумажные сделки (§18, §21, `GET /api/v1/paper/trades`).
  ///
  /// Сопровождение переехало на сервер целиком. На устройстве оно брало
  /// свечи только у биржи напрямую, Bybit отвечает телефону владельца
  /// `403 — CloudFront блокирует доступ из вашей страны`, свечей нет, сверка
  /// не вызывается ни разу — и позиция замирает навсегда: ни тейки, ни стоп,
  /// ни срок не проверяются, а карточка выглядит живой. Так BTCUSDT провисел
  /// несколько дней на «взято тейков: 2 из 3».
  ///
  /// `null` — не «сделок нет», а «спросить не удалось»: пустой список и
  /// молчание движка должны различаться, иначе экран объявит журнал пустым
  /// на ровном месте.
  Future<List<PaperPosition>?> paperTrades({bool liveOnly = true}) async {
    if (!isConfigured) return null;
    try {
      final raw = await _api.getList(
        '$_base/paper/trades?live_only=$liveOnly&limit=100',
      );
      return EngineContract.paperTrades(raw);
    } catch (_) {
      return null;
    }
  }

  /// Состояние загрузки данных: без него пустая выдача неотличима от
  /// «данные не приехали».
  Future<Map<String, dynamic>?> dataStatus() async {
    if (!isConfigured) return null;
    try {
      return await _api.get('$_base/market/status');
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>?> health() async {
    if (!isConfigured) return null;
    try {
      return await _api.get('/health');
    } catch (_) {
      return null;
    }
  }

  static String _reason(Object error) {
    final text = error.toString();
    // Причина показывается дословно: «что-то пошло не так» не позволяет
    // отличить обрыв связи от сломанного движка, а это разные действия.
    return text.length > 200 ? '${text.substring(0, 200)}…' : text;
  }
}
