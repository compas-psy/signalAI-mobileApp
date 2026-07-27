import '../../domain/broker/broker.dart';
import '../../domain/ledger/ledger_event.dart';
import '../../domain/ledger/money.dart';

/// Разбор операций брокера в события книги.
///
/// Отдельный слой, а не метод брокера: домен книги не должен знать, как
/// Т-Инвестиции называют комиссию, а брокер — что такое контур и пакет.
/// Здесь и только здесь живёт соответствие «строка выписки → запись книги».
///
/// Правило неизвестного типа: операция, которую мы не умеем классифицировать,
/// не выбрасывается и не подгоняется под похожий тип. Она попадает в книгу
/// корректировкой с исходным описанием брокера — деньги сходятся, а строка
/// честно говорит, что её смысл не разобран.
abstract final class BrokerImport {
  /// Идентификатор события книги по операции брокера.
  ///
  /// Префикс площадки обязателен: идентификаторы у Bybit и Т-Инвестиций
  /// независимы и однажды совпадут.
  static String eventId(String venue, String operationId) => '$venue:$operationId';

  /// Превращает операции счёта в записи книги.
  ///
  /// [venue] задаёт и префикс идентификатора, и таблицу типов: у площадок
  /// разные словари, и подставлять один под другой нельзя.
  static List<LedgerEvent> fromOperations({
    required String accountId,
    required String venue,
    required List<BrokerOperation> operations,
  }) {
    final result = <LedgerEvent>[];
    for (final op in operations) {
      if (op.id.isEmpty) continue;
      final currency = Currency.parse(op.currency);
      final cash = Money.of(op.payment, currency);
      final type = venue == 'bybit' ? _bybitTypeOf(op.type) : _typeOf(op.type);

      result.add(LedgerEvent(
        id: eventId(venue, op.id),
        type: type,
        effectiveAt: op.at,
        receivedAt: DateTime.now().toUtc(),
        accountId: accountId,
        cashImpact: cash,
        instrument: op.instrument.isEmpty ? null : op.instrument,
        quantity: op.quantity == 0 ? null : Quantity.of(op.quantity.abs()),
        price: op.price == 0 ? null : Money.of(op.price, currency),
        source: LedgerSource.broker,
        // Импортированное событие сверено по построению: оно пришло от
        // площадки, а не введено руками.
        reconcile: ReconcileStatus.matched,
        brokerRef: op.id,
        note: type == LedgerEventType.correction && op.description.isNotEmpty
            ? 'не разобрано: ${op.description} (${op.type})'
            : null,
      ));
    }
    return result;
  }

  /// Соответствие типов Т-Инвестиций типам книги.
  ///
  /// Список закрытый и явный: молчаливое «всё остальное — комиссия» однажды
  /// превратит дивиденд в издержку и тихо занизит результат.
  static LedgerEventType _typeOf(String raw) => switch (raw) {
        'OPERATION_TYPE_BUY' ||
        'OPERATION_TYPE_BUY_CARD' ||
        'OPERATION_TYPE_BUY_MARGIN' =>
          LedgerEventType.buy,
        'OPERATION_TYPE_SELL' ||
        'OPERATION_TYPE_SELL_CARD' ||
        'OPERATION_TYPE_SELL_MARGIN' =>
          LedgerEventType.sell,
        'OPERATION_TYPE_INPUT' => LedgerEventType.deposit,
        'OPERATION_TYPE_OUTPUT' => LedgerEventType.withdrawal,
        'OPERATION_TYPE_BROKER_FEE' ||
        'OPERATION_TYPE_SERVICE_FEE' ||
        'OPERATION_TYPE_MARGIN_FEE' ||
        'OPERATION_TYPE_SUCCESS_FEE' ||
        'OPERATION_TYPE_TRACK_MFEE' ||
        'OPERATION_TYPE_TRACK_PFEE' ||
        'OPERATION_TYPE_ADVICE_FEE' =>
          LedgerEventType.fee,
        'OPERATION_TYPE_DIVIDEND' || 'OPERATION_TYPE_DIVIDEND_TRANSFER' =>
          LedgerEventType.dividend,
        'OPERATION_TYPE_COUPON' || 'OPERATION_TYPE_BOND_REPAYMENT' ||
        'OPERATION_TYPE_BOND_REPAYMENT_FULL' =>
          LedgerEventType.coupon,
        'OPERATION_TYPE_ACCRUING_VARMARGIN' ||
        'OPERATION_TYPE_WRITING_OFF_VARMARGIN' =>
          LedgerEventType.variationMargin,
        'OPERATION_TYPE_TAX' ||
        'OPERATION_TYPE_TAX_CORRECTION' ||
        'OPERATION_TYPE_TAX_PROGRESSIVE' ||
        'OPERATION_TYPE_DIVIDEND_TAX' ||
        'OPERATION_TYPE_DIVIDEND_TAX_PROGRESSIVE' ||
        'OPERATION_TYPE_BOND_TAX' ||
        'OPERATION_TYPE_BENEFIT_TAX' =>
          LedgerEventType.tax,
        'OPERATION_TYPE_OVERNIGHT' || 'OPERATION_TYPE_INTEREST' =>
          LedgerEventType.interest,
        // Тип неизвестен — деньги учитываем, смысл не выдумываем.
        _ => LedgerEventType.correction,
      };

  /// Соответствие типов журнала Bybit типам книги.
  ///
  /// У биржи это не выписка, а журнал изменений баланса: строка TRADE — это
  /// исполнение вместе с комиссией, SETTLEMENT — фандинг бессрочного
  /// контракта, TRANSFER_* — движение между кошельками, а не прибыль.
  static LedgerEventType _bybitTypeOf(String raw) => switch (raw.toUpperCase()) {
        // Сделка деривативом не меняет лоты «бумаги»: результат приходит
        // изменением баланса, поэтому это вармаржа, а не покупка актива.
        'TRADE' || 'DELIVERY' || 'LIQUIDATION' || 'BUST_TRADE' || 'SETTLEMENT_PNL' =>
          LedgerEventType.variationMargin,
        'SETTLEMENT' || 'FUNDING' || 'FUNDING_FEE' => LedgerEventType.funding,
        'TRANSFER_IN' => LedgerEventType.deposit,
        'TRANSFER_OUT' => LedgerEventType.withdrawal,
        'INTEREST' => LedgerEventType.interest,
        'FEE' || 'TRADE_FEE' || 'COMMISSION' => LedgerEventType.fee,
        _ => LedgerEventType.correction,
      };

  /// Сколько операций осталось неразобранными — для честной подписи импорта.
  static int unresolved(List<LedgerEvent> events) => events
      .where((e) => e.type == LedgerEventType.correction && e.source == LedgerSource.broker)
      .length;
}
