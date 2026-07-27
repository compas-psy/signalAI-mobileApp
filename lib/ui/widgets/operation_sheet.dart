import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/ledger/account.dart';
import '../../domain/ledger/ledger_event.dart';
import '../../domain/ledger/money.dart';
import '../../state/app_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Ввод операции в книгу.
///
/// Ручной ввод — не костыль, а обязательная часть модели: банковский резерв,
/// перевод между площадками и дивиденд, который брокер показал только в
/// годовом отчёте, иначе в капитал не попадут никогда. Сумма вводится
/// строкой и разбирается без потери копеек.
Future<bool> showOperationSheet(BuildContext context, AppController controller) async {
  final result = await showModalSheet<bool>(
    context,
    (context) => _OperationForm(controller: controller),
  );
  return result ?? false;
}

/// Разбор операции: всё, что о ней знает книга.
Future<void> showOperationDetail(BuildContext context, LedgerEvent event) =>
    showModalSheet<void>(context, (context) => _OperationDetail(event: event));

/// Заведение счёта, которого нет у площадки.
Future<void> showAddAccountSheet(BuildContext context, AppController controller) =>
    showModalSheet<void>(context, (context) => _AccountForm(controller: controller));

/// Общий контейнер модальных листов раздела.
Future<Res?> showModalSheet<Res>(BuildContext context, WidgetBuilder builder) =>
    Navigator.of(context).push<Res>(
      PageRouteBuilder<Res>(
        opaque: false,
        barrierColor: const Color(0x99000000),
        barrierDismissible: true,
        pageBuilder: (context, animation, secondary) => SafeArea(
          child: Align(
            alignment: Alignment.bottomCenter,
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 460),
              child: Container(
                margin: const EdgeInsets.all(10),
                padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
                decoration: BoxDecoration(
                  color: C.sheet,
                  border: Border.all(color: C.border),
                  borderRadius: BorderRadius.circular(R.sheet),
                ),
                child: builder(context),
              ),
            ),
          ),
        ),
        transitionsBuilder: (context, animation, secondary, child) => FadeTransition(
          opacity: animation,
          child: SlideTransition(
            position: Tween(begin: const Offset(0, 0.06), end: Offset.zero)
                .animate(CurvedAnimation(parent: animation, curve: Curves.easeOut)),
            child: child,
          ),
        ),
      ),
    );

class _OperationForm extends StatefulWidget {
  const _OperationForm({required this.controller});

  final AppController controller;

  @override
  State<_OperationForm> createState() => _OperationFormState();
}

class _OperationFormState extends State<_OperationForm> {
  // Типы, которые владелец заводит руками. Сделки приходят от брокера —
  // вводить их вручную можно, но в первую очередь нужны деньги и доходы.
  static const _types = [
    LedgerEventType.opening,
    LedgerEventType.deposit,
    LedgerEventType.withdrawal,
    LedgerEventType.transfer,
    LedgerEventType.dividend,
    LedgerEventType.coupon,
    LedgerEventType.interest,
    LedgerEventType.fee,
    LedgerEventType.tax,
    LedgerEventType.correction,
  ];

  final _amount = TextEditingController();
  final _note = TextEditingController();

  LedgerEventType _type = LedgerEventType.deposit;
  String? _accountId;
  Contour? _contour;
  String? _error;
  bool _busy = false;

  @override
  void dispose() {
    _amount.dispose();
    _note.dispose();
    super.dispose();
  }

  /// Знак операции задаётся её типом, а не пользователем: вывод не может быть
  /// положительным, комиссия не может увеличивать деньги.
  bool get _negative =>
      _type == LedgerEventType.withdrawal ||
      _type == LedgerEventType.fee ||
      _type == LedgerEventType.tax;

  Future<void> _submit() async {
    final accounts = widget.controller.capitalDesk?.accounts ?? const <Account>[];
    final accountId = _accountId ?? accounts.firstOrNull?.id;
    if (accountId == null) {
      setState(() => _error = 'Сначала заведите счёт: операция должна где-то произойти');
      return;
    }
    final raw = _amount.text.trim();
    if (raw.isEmpty) {
      setState(() => _error = 'Введите сумму');
      return;
    }
    final account = accounts.firstWhere(
      (a) => a.id == accountId,
      orElse: () => Account(
        id: accountId,
        title: accountId,
        kind: AccountKind.manual,
        currency: Currency.rub,
      ),
    );
    Money amount;
    try {
      amount = Money.parse(raw, account.currency);
    } on FormatException {
      setState(() => _error = 'Сумма не разобралась: ожидается «12 500,40»');
      return;
    }
    if (amount.isZero) {
      setState(() => _error = 'Нулевая операция ничего не меняет');
      return;
    }
    final signed = _negative ? -amount.abs : amount;

    setState(() {
      _busy = true;
      _error = null;
    });
    final ok = await widget.controller.recordOperation(
      type: _type,
      accountId: accountId,
      cashImpact: signed,
      contour: _contour,
      note: _note.text.trim().isEmpty ? null : _note.text.trim(),
    );
    if (!mounted) return;
    if (ok) {
      Navigator.of(context).pop(true);
    } else {
      setState(() {
        _busy = false;
        _error = 'Книга недоступна: записать некуда';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final accounts = widget.controller.capitalDesk?.accounts ?? const <Account>[];
    _accountId ??= accounts.firstOrNull?.id;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('Операция в книгу', style: T.jost(18)),
        const SizedBox(height: 4),
        Text(
          'Запись неизменяема: исправить её потом можно только компенсирующей '
          'операцией.',
          style: T.body(11, color: C.muted, height: 1.4),
        ),
        const SizedBox(height: 14),
        const SectionLabel('Тип'),
        const SizedBox(height: 8),
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            for (final type in _types)
              _Chip(
                label: type.label,
                active: type == _type,
                onTap: () => setState(() => _type = type),
              ),
          ],
        ),
        if (accounts.length > 1) ...[
          const SizedBox(height: 14),
          const SectionLabel('Счёт'),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final account in accounts)
                _Chip(
                  label: account.title,
                  active: account.id == _accountId,
                  onTap: () => setState(() => _accountId = account.id),
                ),
            ],
          ),
        ],
        const SizedBox(height: 14),
        const SectionLabel('Контур'),
        const SizedBox(height: 8),
        Wrap(
          spacing: 6,
          runSpacing: 6,
          children: [
            _Chip(
              label: 'не задан',
              active: _contour == null,
              onTap: () => setState(() => _contour = null),
            ),
            for (final contour in Contour.values)
              _Chip(
                label: contour.label,
                active: contour == _contour,
                onTap: () => setState(() => _contour = contour),
              ),
          ],
        ),
        const SizedBox(height: 14),
        _Field(
          label: _negative ? 'Сумма (спишется)' : 'Сумма (зачислится)',
          controller: _amount,
          hint: '12 500,40',
          number: true,
        ),
        const SizedBox(height: 10),
        _Field(label: 'Комментарий', controller: _note, hint: 'необязательно'),
        if (_error != null) ...[
          const SizedBox(height: 10),
          Text(_error!, style: T.body(11.5, color: C.red, height: 1.4)),
        ],
        const SizedBox(height: 16),
        Row(
          children: [
            Expanded(
              child: Pressable(
                onTap: _busy ? null : _submit,
                child: Container(
                  padding: const EdgeInsets.symmetric(vertical: 13),
                  decoration: BoxDecoration(
                    color: _busy ? C.chip : C.accent,
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Center(
                    child: Text(
                      _busy ? 'Записываем…' : 'Записать',
                      style: T.body(13.5,
                          weight: 800, color: _busy ? C.muted : C.onAccent),
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 9),
            Pressable(
              onTap: () => Navigator.of(context).pop(false),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 13),
                decoration: BoxDecoration(
                  border: Border.all(color: C.border),
                  borderRadius: BorderRadius.circular(R.button),
                ),
                child: Text('Отмена', style: T.body(13.5, weight: 700, color: C.muted)),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

class _OperationDetail extends StatelessWidget {
  const _OperationDetail({required this.event});

  final LedgerEvent event;

  @override
  Widget build(BuildContext context) {
    final local = event.effectiveAt.toLocal();
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(event.type.label, style: T.jost(18)),
        const SizedBox(height: 2),
        Text(
          '${local.day.toString().padLeft(2, '0')}.'
          '${local.month.toString().padLeft(2, '0')}.${local.year} '
          '${local.hour.toString().padLeft(2, '0')}:'
          '${local.minute.toString().padLeft(2, '0')}',
          style: T.mono(11, color: C.muted),
        ),
        const SizedBox(height: 14),
        KeyValueRow(
          name: 'Влияние на деньги',
          value: fmtMoney(event.cashImpact, sign: true),
          valueStyle: T.mono(12.5,
              weight: 700, color: event.cashImpact.isNegative ? C.red : C.green),
        ),
        KeyValueRow(name: 'Счёт', value: event.accountId),
        if (event.instrument != null)
          KeyValueRow(name: 'Инструмент', value: event.instrument!),
        if (event.quantity != null)
          KeyValueRow(name: 'Количество', value: fmtQuantity(event.quantity!)),
        if (event.price != null)
          KeyValueRow(name: 'Цена', value: fmtMoney(event.price!)),
        if (event.fee != null)
          KeyValueRow(name: 'Комиссия', value: fmtMoney(event.fee!)),
        if (event.tax != null)
          KeyValueRow(name: 'Налог', value: fmtMoney(event.tax!)),
        if (event.contour != null)
          KeyValueRow(name: 'Контур', value: event.contour!.label),
        KeyValueRow(name: 'Источник', value: event.source.label),
        KeyValueRow(name: 'Сверка', value: event.reconcile.label),
        if (event.brokerRef != null)
          KeyValueRow(name: 'Ссылка брокера', value: event.brokerRef!),
        if (event.correctsId != null)
          KeyValueRow(name: 'Исправляет запись', value: event.correctsId!),
        KeyValueRow(name: 'Идентификатор', value: event.id, showDivider: false),
        if (event.note != null) ...[
          const SizedBox(height: 10),
          Text(event.note!, style: T.body(11.5, color: C.textSecondary, height: 1.4)),
        ],
        const SizedBox(height: 14),
        Text(
          'Запись неизменяема. Если она ошибочна, в книгу добавляется '
          'компенсирующая операция со ссылкой на эту — история остаётся '
          'проверяемой.',
          style: T.body(10.5, color: C.faint, height: 1.4),
        ),
        const SizedBox(height: 14),
        Pressable(
          onTap: () => Navigator.of(context).pop(),
          child: Container(
            padding: const EdgeInsets.symmetric(vertical: 12),
            decoration: BoxDecoration(
              border: Border.all(color: C.border),
              borderRadius: BorderRadius.circular(R.button),
            ),
            child: Center(
              child: Text('Закрыть', style: T.body(13, weight: 700, color: C.muted)),
            ),
          ),
        ),
      ],
    );
  }
}

class _AccountForm extends StatefulWidget {
  const _AccountForm({required this.controller});

  final AppController controller;

  @override
  State<_AccountForm> createState() => _AccountFormState();
}

class _AccountFormState extends State<_AccountForm> {
  final _title = TextEditingController();
  AccountKind _kind = AccountKind.bank;
  String? _error;

  static const _kinds = [
    AccountKind.bank,
    AccountKind.wallet,
    AccountKind.manual,
    AccountKind.iis,
    AccountKind.paper,
  ];

  @override
  void dispose() {
    _title.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final title = _title.text.trim();
    if (title.isEmpty) {
      setState(() => _error = 'Введите название счёта');
      return;
    }
    await widget.controller.addAccount(Account(
      id: 'manual-${DateTime.now().microsecondsSinceEpoch}',
      title: title,
      kind: _kind,
      currency: Currency.rub,
      reconcile: ReconcileStatus.manual,
    ));
    if (mounted) Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('Новый счёт', style: T.jost(18)),
          const SizedBox(height: 4),
          Text(
            'Счета площадок появляются сами после сверки. Здесь заводится то, '
            'что не отдаёт ни один API: банковский резерв, кошелёк, ИИС в '
            'другом банке.',
            style: T.body(11, color: C.muted, height: 1.4),
          ),
          const SizedBox(height: 14),
          const SectionLabel('Тип'),
          const SizedBox(height: 8),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final kind in _kinds)
                _Chip(
                  label: kind.label,
                  active: kind == _kind,
                  onTap: () => setState(() => _kind = kind),
                ),
            ],
          ),
          const SizedBox(height: 14),
          _Field(label: 'Название', controller: _title, hint: 'Резерв в банке'),
          if (_error != null) ...[
            const SizedBox(height: 10),
            Text(_error!, style: T.body(11.5, color: C.red)),
          ],
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: Pressable(
                  onTap: _submit,
                  child: Container(
                    padding: const EdgeInsets.symmetric(vertical: 13),
                    decoration: BoxDecoration(
                      color: C.accent,
                      borderRadius: BorderRadius.circular(R.button),
                    ),
                    child: Center(
                      child: Text('Добавить',
                          style: T.body(13.5, weight: 800, color: C.onAccent)),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 9),
              Pressable(
                onTap: () => Navigator.of(context).pop(),
                child: Container(
                  padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 13),
                  decoration: BoxDecoration(
                    border: Border.all(color: C.border),
                    borderRadius: BorderRadius.circular(R.button),
                  ),
                  child: Text('Отмена',
                      style: T.body(13.5, weight: 700, color: C.muted)),
                ),
              ),
            ],
          ),
        ],
      );
}

class _Chip extends StatelessWidget {
  const _Chip({required this.label, required this.active, required this.onTap});

  final String label;
  final bool active;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: Container(
          constraints: const BoxConstraints(minHeight: 34),
          alignment: Alignment.center,
          padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
          decoration: BoxDecoration(
            color: active ? C.accentFaint : C.inset,
            border: Border.all(color: active ? C.accentBorder : C.border),
            borderRadius: BorderRadius.circular(R.chip),
          ),
          child: Text(
            label,
            style: T.body(11.5, weight: 700, color: active ? C.accent : C.muted),
          ),
        ),
      );
}

class _Field extends StatelessWidget {
  const _Field({
    required this.label,
    required this.controller,
    this.hint,
    this.number = false,
  });

  final String label;
  final TextEditingController controller;
  final String? hint;
  final bool number;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionLabel(label),
          const SizedBox(height: 6),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            decoration: BoxDecoration(
              color: C.inset,
              border: Border.all(color: C.border),
              borderRadius: BorderRadius.circular(R.inner),
            ),
            child: EditableText(
              controller: controller,
              focusNode: FocusNode(),
              style: number ? T.mono(14, weight: 600) : T.body(13),
              cursorColor: C.accent,
              backgroundCursorColor: C.dim,
              keyboardType: number ? TextInputType.text : TextInputType.text,
              maxLines: 1,
              selectionColor: C.accentFaint,
            ),
          ),
          if (hint != null) ...[
            const SizedBox(height: 4),
            Text(hint!, style: T.body(10, color: C.faint)),
          ],
        ],
      );
}
