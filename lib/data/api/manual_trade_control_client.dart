import 'api_client.dart';

/// Owner actions allowed after entry.
///
/// Every action is monotonic: it can only reduce exposure, tighten protection,
/// or hand management back to the automatic policy. There is intentionally no
/// action for increasing exposure or widening a stop.
enum ManualTradeAction {
  close('CLOSE'),
  reduce('REDUCE'),
  tightenStop('TIGHTEN_STOP'),
  returnAuto('RETURN_AUTO');

  const ManualTradeAction(this.wireName);

  final String wireName;

  static ManualTradeAction parse(Object? raw) {
    if (raw is! String) {
      throw ApiException('Сервер не указал тип ручного действия.');
    }
    for (final value in ManualTradeAction.values) {
      if (value.wireName == raw) return value;
    }
    throw ApiException('Сервер вернул неизвестное ручное действие: $raw.');
  }
}

/// Durable server acknowledgement of an owner command.
///
/// `REQUESTED` means exactly that: the command is persisted but the exchange
/// has not yet confirmed execution. The client must never turn this into a
/// local “filled/closed” state by itself.
class ManualTradeControlResult {
  const ManualTradeControlResult({
    required this.commandId,
    required this.intentId,
    required this.managementPolicySnapshotId,
    required this.action,
    required this.status,
    required this.reduceOnly,
    required this.quantity,
    required this.stopPrice,
    required this.orderId,
    required this.orderStatus,
    required this.created,
  });

  final String commandId;
  final String intentId;
  final String managementPolicySnapshotId;
  final ManualTradeAction action;
  final String status;
  final bool reduceOnly;
  final String? quantity;
  final String? stopPrice;
  final String? orderId;
  final String? orderStatus;
  final bool created;

  factory ManualTradeControlResult.fromJson(Map<String, dynamic> json) {
    String requiredString(String key) {
      final value = json[key];
      if (value is! String || value.trim().isEmpty) {
        throw ApiException('Сервер вернул manual control без поля $key.');
      }
      return value;
    }

    String? optionalString(String key) {
      final value = json[key];
      if (value == null) return null;
      if (value is! String || value.trim().isEmpty) {
        throw ApiException('Сервер вернул некорректное поле $key.');
      }
      return value;
    }

    if (json['reduce_only'] != true) {
      throw ApiException(
        'Сервер не подтвердил reduce-only семантику ручного действия.',
      );
    }
    if (json['created'] is! bool) {
      throw ApiException('Сервер не указал идемпотентный результат команды.');
    }

    return ManualTradeControlResult(
      commandId: requiredString('command_id'),
      intentId: requiredString('intent_id'),
      managementPolicySnapshotId: requiredString(
        'management_policy_snapshot_id',
      ),
      action: ManualTradeAction.parse(json['action']),
      status: requiredString('status'),
      reduceOnly: true,
      quantity: optionalString('quantity'),
      stopPrice: optionalString('stop_price'),
      orderId: optionalString('order_id'),
      orderStatus: optionalString('order_status'),
      created: json['created'] as bool,
    );
  }
}

/// Thin owner client for B8.5.
///
/// It binds by idea id, not by an execution id supplied or guessed by the
/// phone. The server resolves the unique active protected execution and fails
/// closed if that binding is absent or ambiguous.
class ManualTradeControlClient {
  ManualTradeControlClient({ApiClient? api}) : _api = api ?? ApiClient();

  final ApiClient _api;

  Future<ManualTradeControlResult> request({
    required String ideaId,
    required ManualTradeAction action,
    required String reason,
    required String idempotencyKey,
    String? quantity,
    String? stopPrice,
  }) async {
    final idea = ideaId.trim();
    final why = reason.trim();
    final key = idempotencyKey.trim();
    final qty = quantity?.trim();
    final stop = stopPrice?.trim();

    if (idea.isEmpty) throw ApiException('Не указана идея открытой сделки.');
    if (why.isEmpty) throw ApiException('Для ручного действия нужна причина.');
    if (key.isEmpty) throw ApiException('Для ручного действия нужен replay key.');

    switch (action) {
      case ManualTradeAction.close:
      case ManualTradeAction.returnAuto:
        if (qty != null || stop != null) {
          throw ApiException('${action.wireName} не принимает цену или объём.');
        }
      case ManualTradeAction.reduce:
        if (qty == null || qty.isEmpty || stop != null) {
          throw ApiException('REDUCE требует только объём сокращения.');
        }
      case ManualTradeAction.tightenStop:
        if (stop == null || stop.isEmpty || qty != null) {
          throw ApiException('TIGHTEN_STOP требует только новую цену стопа.');
        }
    }

    final body = <String, dynamic>{
      'action': action.wireName,
      if (qty != null) 'quantity': qty,
      if (stop != null) 'stop_price': stop,
      'reason': why,
    };
    final json = await _api.post(
      '/api/v1/execution/ideas/${Uri.encodeComponent(idea)}/control',
      body: body,
      idempotencyKey: key,
    );
    final result = ManualTradeControlResult.fromJson(json);
    if (result.action != action) {
      throw ApiException(
        'Сервер подтвердил другое ручное действие, чем запросил владелец.',
      );
    }
    return result;
  }
}
