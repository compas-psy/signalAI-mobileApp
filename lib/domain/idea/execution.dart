/// Состояние исполнения плана (ТЗ §11.3).
///
/// Порядок жёсткий: подтверждение → предпроверка → отправка входа →
/// исполнение → **постановка защиты** → сопровождение. Защита стоит отдельным
/// состоянием, а не побочным эффектом входа, ровно потому, что открытая
/// позиция без стопа — единственный способ потерять больше запланированного.
/// Любая ошибка уводит в безопасное состояние, а не «дальше по циклу».
enum ExecutionState {
  pendingConfirmation('Ждёт подтверждения', 'План показан, решение не принято'),
  precheck('Предпроверка', 'Идёт финальная проверка перед отправкой'),
  submitEntry('Вход отправлен', 'Заявка на вход у брокера'),
  entryPartial('Вход частичный', 'Исполнена часть объёма'),
  entryFilled('Вход исполнен', 'Объём набран полностью'),
  placeProtection('Ставим защиту', 'Выставляются стоп и цели'),
  active('В работе', 'Позиция открыта, защита стоит'),
  tp1('TP1 взят', 'Первая цель исполнена'),
  stopAtBreakeven('Стоп в безубытке', 'Стоп перенесён с учётом комиссий'),
  tp2('TP2 взят', 'Вторая цель исполнена'),
  trailing('Трейлинг', 'Стоп ведётся по структуре'),
  closed('Закрыто', 'Позиции нет'),
  safeState('Безопасное состояние', 'Ошибка исполнения, идёт сверка');

  const ExecutionState(this.label, this.meaning);

  final String label;
  final String meaning;

  static const _withPosition = {
    ExecutionState.entryPartial,
    ExecutionState.entryFilled,
    ExecutionState.placeProtection,
    ExecutionState.active,
    ExecutionState.tp1,
    ExecutionState.stopAtBreakeven,
    ExecutionState.tp2,
    ExecutionState.trailing,
  };

  static const _protected = {
    ExecutionState.active,
    ExecutionState.tp1,
    ExecutionState.stopAtBreakeven,
    ExecutionState.tp2,
    ExecutionState.trailing,
  };

  /// Открыта ли позиция.
  bool get hasPosition => _withPosition.contains(this);

  /// Стоит ли защита у брокера.
  bool get isProtected => _protected.contains(this);

  /// Позиция открыта, защиты нет. Это окно и есть то, ради чего существуют
  /// SLA и компенсирующее закрытие (ТЗ §21).
  bool get isNaked => hasPosition && !isProtected;

  bool get isTerminal =>
      this == ExecutionState.closed || this == ExecutionState.safeState;

  /// Разрешённые переходы (ТЗ §11.3).
  Set<ExecutionState> get allowedNext => switch (this) {
        ExecutionState.pendingConfirmation => {
            ExecutionState.precheck,
            ExecutionState.closed,
          },
        // Предпроверка не пропустила — это отказ до денег: позиции нет,
        // сверять нечего, идея просто закрывается.
        ExecutionState.precheck => {
            ExecutionState.submitEntry,
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        ExecutionState.submitEntry => {
            ExecutionState.entryPartial,
            ExecutionState.entryFilled,
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        ExecutionState.entryPartial => {
            ExecutionState.entryFilled,
            ExecutionState.placeProtection,
            ExecutionState.safeState,
          },
        ExecutionState.entryFilled => {
            ExecutionState.placeProtection,
            ExecutionState.safeState,
          },
        ExecutionState.placeProtection => {
            ExecutionState.active,
            ExecutionState.safeState,
          },
        ExecutionState.active => {
            ExecutionState.tp1,
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        ExecutionState.tp1 => {
            ExecutionState.stopAtBreakeven,
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        ExecutionState.stopAtBreakeven => {
            ExecutionState.tp2,
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        ExecutionState.tp2 => {
            ExecutionState.trailing,
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        ExecutionState.trailing => {
            ExecutionState.closed,
            ExecutionState.safeState,
          },
        // Из безопасного состояния выход только через сверку в «закрыто»:
        // продолжать торговать после ошибки исполнения нельзя.
        ExecutionState.safeState => {ExecutionState.closed},
        ExecutionState.closed => const {},
      };

  bool canMoveTo(ExecutionState next) => allowedNext.contains(next);

  static ExecutionState parse(String? name) =>
      ExecutionState.values.where((s) => s.name == name).firstOrNull ??
      ExecutionState.pendingConfirmation;
}

/// Состояние защитных заявок у брокера.
enum ProtectionStatus {
  none('Нет', 'Защитных заявок у брокера нет'),
  placing('Ставится', 'Заявки отправлены, подтверждения нет'),
  placed('Стоит', 'Стоп и цели приняты биржей'),
  failed('Не поставлена', 'Брокер отверг защитные заявки');

  const ProtectionStatus(this.label, this.meaning);

  final String label;
  final String meaning;

  static ProtectionStatus parse(String? name) =>
      ProtectionStatus.values.where((s) => s.name == name).firstOrNull ??
      ProtectionStatus.none;
}

/// Одно исполнение по заявке.
class Fill {
  const Fill({
    required this.quantity,
    required this.price,
    required this.at,
    this.fee = 0,
  });

  final int quantity;
  final double price;
  final DateTime at;
  final double fee;

  Map<String, dynamic> toJson() => {
        'quantity': quantity,
        'price': price,
        'at': at.toIso8601String(),
        'fee': fee,
      };

  static Fill fromJson(Map<String, dynamic> j) => Fill(
        quantity: (j['quantity'] as num?)?.toInt() ?? 0,
        price: (j['price'] as num?)?.toDouble() ?? 0,
        at: DateTime.tryParse(j['at'] as String? ?? '') ?? DateTime(2000),
        fee: (j['fee'] as num?)?.toDouble() ?? 0,
      );
}

/// Исполнение плана (ТЗ §17, §21).
///
/// Объект неизменяемый: каждый шаг возвращает новое состояние. Менять
/// исполнение на месте значит потерять историю ровно там, где она нужна для
/// сверки с брокером.
class Execution {
  const Execution({
    required this.ideaId,
    required this.planHash,
    required this.plannedQuantity,
    required this.state,
    this.protection = ProtectionStatus.none,
    this.fills = const [],
    this.brokerOrderIds = const [],
    this.version = 0,
    this.note = '',
    this.entryFilledAt,
  });

  final String ideaId;

  /// Отпечаток подписанного плана. Сверка с брокером идёт по нему.
  final String planHash;

  /// Объём, который планировалось набрать.
  final int plannedQuantity;

  final ExecutionState state;
  final ProtectionStatus protection;
  final List<Fill> fills;
  final List<String> brokerOrderIds;

  /// Версия для оптимистичной блокировки (ТЗ §21).
  final int version;

  /// Почему исполнение оказалось в этом состоянии.
  final String note;

  /// Когда набрался объём — от этого отсчитывается SLA на защиту.
  final DateTime? entryFilledAt;

  int get filledQuantity => fills.fold(0, (sum, f) => sum + f.quantity);

  int get remainingQuantity {
    final left = plannedQuantity - filledQuantity;
    return left < 0 ? 0 : left;
  }

  bool get isFullyFilled =>
      plannedQuantity > 0 && filledQuantity >= plannedQuantity;

  /// Средняя цена входа. null — не исполнено ничего.
  double? get averageEntry {
    if (fills.isEmpty || filledQuantity == 0) return null;
    var sum = 0.0;
    for (final fill in fills) {
      sum += fill.price * fill.quantity;
    }
    return sum / filledQuantity;
  }

  /// Уплаченные комиссии.
  double get fees => fills.fold(0.0, (sum, f) => sum + f.fee);

  /// Ключ идемпотентности операции (ТЗ §21).
  ///
  /// Повторная отправка того же шага не создаёт вторую заявку: сеть рвётся
  /// чаще, чем хотелось бы, а двойной вход — двойной риск.
  String idempotencyKey(String operation) => '$planHash:$operation:$version';

  /// Объём защитных заявок.
  ///
  /// ТЗ §21: частичное исполнение пересчитывает защиту, но **не увеличивает**
  /// исходный риск. Защита ставится на набранный объём, а не на плановый:
  /// стоп на десять контрактов при четырёх набранных — это шорт на шесть.
  int get protectiveQuantity => filledQuantity;

  Execution copyWith({
    ExecutionState? state,
    ProtectionStatus? protection,
    List<Fill>? fills,
    List<String>? brokerOrderIds,
    String? note,
    DateTime? entryFilledAt,
  }) =>
      Execution(
        ideaId: ideaId,
        planHash: planHash,
        plannedQuantity: plannedQuantity,
        state: state ?? this.state,
        protection: protection ?? this.protection,
        fills: fills ?? this.fills,
        brokerOrderIds: brokerOrderIds ?? this.brokerOrderIds,
        version: version + 1,
        note: note ?? this.note,
        entryFilledAt: entryFilledAt ?? this.entryFilledAt,
      );

  /// Переход состояния с проверкой машины (ТЗ §11.3).
  Execution moveTo(ExecutionState next, {String note = ''}) {
    if (!state.canMoveTo(next)) {
      throw StateError(
          'переход ${state.name} → ${next.name} запрещён (ТЗ §11.3)');
    }
    return copyWith(state: next, note: note);
  }

  /// Ошибка исполнения: безопасное состояние и сверка (ТЗ §11.3).
  ///
  /// Доступна из любого состояния. Ошибку нельзя проглотить: непоставленный
  /// стоп или потерянная заявка обязаны остановить автоматику.
  Execution toSafeState(String reason) =>
      copyWith(state: ExecutionState.safeState, note: reason);

  /// Добавить исполнение и перевести состояние по факту набранного объёма.
  Execution addFill(Fill fill, {DateTime? at}) {
    final next = [...fills, fill];
    final filled = next.fold(0, (sum, f) => sum + f.quantity);

    // Перебор объёма — ошибка брокера или наша: продолжать нельзя.
    if (plannedQuantity > 0 && filled > plannedQuantity) {
      return copyWith(
        fills: next,
        state: ExecutionState.safeState,
        note: 'исполнено $filled при плановых $plannedQuantity',
      );
    }

    final target = filled >= plannedQuantity
        ? ExecutionState.entryFilled
        : ExecutionState.entryPartial;

    if (state != target && !state.canMoveTo(target)) {
      return copyWith(
        fills: next,
        state: ExecutionState.safeState,
        note: 'исполнение пришло в состоянии ${state.label}',
      );
    }

    return copyWith(
      fills: next,
      state: target,
      entryFilledAt: target == ExecutionState.entryFilled
          ? (at ?? fill.at)
          : entryFilledAt,
    );
  }

  Map<String, dynamic> toJson() => {
        'idea_id': ideaId,
        'plan_hash': planHash,
        'planned_quantity': plannedQuantity,
        'state': state.name,
        'protection': protection.name,
        'version': version,
        'note': note,
        'broker_order_ids': brokerOrderIds,
        'fills': [for (final f in fills) f.toJson()],
        if (entryFilledAt != null)
          'entry_filled_at': entryFilledAt!.toIso8601String(),
      };

  static Execution fromJson(Map<String, dynamic> j) => Execution(
        ideaId: j['idea_id'] as String? ?? '',
        planHash: j['plan_hash'] as String? ?? '',
        plannedQuantity: (j['planned_quantity'] as num?)?.toInt() ?? 0,
        state: ExecutionState.parse(j['state'] as String?),
        protection: ProtectionStatus.parse(j['protection'] as String?),
        version: (j['version'] as num?)?.toInt() ?? 0,
        note: j['note'] as String? ?? '',
        brokerOrderIds: [
          for (final id in j['broker_order_ids'] as List? ?? const [])
            id as String,
        ],
        fills: [
          for (final f in j['fills'] as List? ?? const [])
            Fill.fromJson(f as Map<String, dynamic>),
        ],
        entryFilledAt: DateTime.tryParse(j['entry_filled_at'] as String? ?? ''),
      );
}

/// Правила сопровождения позиции (ТЗ §21.1).
abstract final class ExecutionRules {
  /// Срок, за который защита обязана встать после входа.
  ///
  /// Не уложились — компенсирующее закрытие или аварийный сигнал (ТЗ §21).
  /// Число наше: ТЗ говорит «в SLA», не называя его. Тридцать секунд — уже
  /// долго для голой позиции, но достаточно, чтобы брокер ответил.
  static const protectionSla = Duration(seconds: 30);

  /// Требуется ли аварийное закрытие: позиция открыта, защиты нет, срок вышел.
  static bool needsCompensatingClose(Execution execution, DateTime now) {
    if (!execution.state.isNaked) return false;
    if (execution.protection == ProtectionStatus.placed) return false;
    final since = execution.entryFilledAt;
    if (since == null) return false;
    return now.difference(since) >= protectionSla;
  }

  /// Можно ли увеличить риск в этом состоянии.
  ///
  /// ТЗ §13.1: после TP1 риск не увеличивается. Позиция уже частично
  /// зафиксирована, и «добрать обратно» — новая сделка с новым решением.
  static bool canIncreaseRisk(ExecutionState state) => const {
        ExecutionState.pendingConfirmation,
        ExecutionState.precheck,
        ExecutionState.submitEntry,
        ExecutionState.entryPartial,
      }.contains(state);

  /// Что движок делает в этом состоянии (ТЗ §21.1).
  static String actionOn(ExecutionState state) => switch (state) {
        ExecutionState.precheck => 'сверяем план с рынком и лимитами',
        ExecutionState.submitEntry => 'ждём исполнения входа',
        ExecutionState.entryPartial => 'добираем объём, защита на набранное',
        ExecutionState.entryFilled => 'создать SL и TP1–TP3',
        ExecutionState.placeProtection => 'ждём подтверждения защитных заявок',
        ExecutionState.active => 'сопровождение по плану',
        ExecutionState.tp1 =>
          'закрыть 40%, перенести стоп в безубыток с учётом комиссий',
        ExecutionState.stopAtBreakeven => 'ждём вторую цель',
        ExecutionState.tp2 =>
          'закрыть 40%, вести стоп по подтверждённой структуре 1H',
        ExecutionState.trailing => 'закрыть 20% на TP3 либо ручной разбор',
        ExecutionState.safeState =>
          'не трогать активные заявки у брокера, сверить состояние',
        ExecutionState.closed => 'разобрать сделку в журнале',
        ExecutionState.pendingConfirmation => 'ждём решения владельца',
      };

  /// Стоп в безубытке с учётом комиссий (ТЗ §21.1).
  ///
  /// «Безубыток» — это не цена входа: комиссии уже уплачены, и выход ровно по
  /// входу оставляет минус. Стоп сдвигается на величину издержек в сторону
  /// прибыли.
  static double breakevenStop({
    required double averageEntry,
    required double feesPerUnit,
    required bool long,
  }) =>
      long ? averageEntry + feesPerUnit : averageEntry - feesPerUnit;
}
