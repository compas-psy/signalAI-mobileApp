import 'package:flutter/material.dart';

import '../../data/api/api_client.dart';
import '../../data/api/manual_trade_control_client.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Owner controls for an already-protected open trade (SAI-050 / B8.5).
///
/// This widget intentionally owns no trade math and never mutates the local
/// execution state. It sends only the owner's monotonic intent to the server;
/// a `REQUESTED` response means persisted, not exchange-confirmed.
class ManualTradeControls extends StatefulWidget {
  const ManualTradeControls({
    super.key,
    required this.ideaId,
    this.client,
  });

  final String ideaId;
  final ManualTradeControlClient? client;

  @override
  State<ManualTradeControls> createState() => _ManualTradeControlsState();
}

class _ManualTradeControlsState extends State<ManualTradeControls> {
  late final ManualTradeControlClient _client;
  ApiClient? _ownedApi;
  final TextEditingController _valueController = TextEditingController();

  ManualTradeAction? _inputAction;
  bool _confirmClose = false;
  bool _busy = false;
  String? _message;
  String? _error;

  @override
  void initState() {
    super.initState();
    final injected = widget.client;
    if (injected != null) {
      _client = injected;
    } else {
      final api = ApiClient();
      _ownedApi = api;
      _client = ManualTradeControlClient(api: api);
    }
  }

  @override
  void dispose() {
    _valueController.dispose();
    _ownedApi?.close();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const SectionLabel('Управление сделкой'),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              ActionButton(
                label: 'Закрыть',
                color: C.red,
                dense: true,
                onTap: _busy ? null : _askClose,
              ),
              ActionButton(
                label: 'Сократить',
                dense: true,
                onTap: _busy ? null : () => _openInput(ManualTradeAction.reduce),
              ),
              ActionButton(
                label: 'Подтянуть стоп',
                dense: true,
                onTap: _busy
                    ? null
                    : () => _openInput(ManualTradeAction.tightenStop),
              ),
              ActionButton(
                label: 'Вернуть автоматическое сопровождение',
                dense: true,
                onTap: _busy ? null : _returnAuto,
              ),
            ],
          ),
          if (_confirmClose) ...[
            const SizedBox(height: 10),
            _Notice(
              text: 'Закрытие уменьшает позицию до нуля. Команда будет '
                  'зафиксирована сервером, но считается исполненной только '
                  'после подтверждения биржи.',
              tone: C.warning,
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: ActionButton(
                    label: 'Подтвердить закрытие',
                    primary: true,
                    color: C.red,
                    dense: true,
                    onTap: _busy ? null : _close,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: ActionButton(
                    label: 'Отмена',
                    dense: true,
                    onTap: _busy ? null : _cancelEdit,
                  ),
                ),
              ],
            ),
          ],
          if (_inputAction != null) ...[
            const SizedBox(height: 10),
            TextField(
              controller: _valueController,
              enabled: !_busy,
              keyboardType: const TextInputType.numberWithOptions(decimal: true),
              decoration: InputDecoration(
                labelText: _inputAction == ManualTradeAction.reduce
                    ? 'На сколько сократить'
                    : 'Новый стоп',
                helperText: _inputAction == ManualTradeAction.reduce
                    ? 'Сервер проверит, что объём меньше текущей позиции.'
                    : 'Сервер разрешит только движение стопа к меньшему риску.',
              ),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: ActionButton(
                    label: 'Отправить',
                    primary: true,
                    dense: true,
                    onTap: _busy ? null : _submitInput,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: ActionButton(
                    label: 'Отмена',
                    dense: true,
                    onTap: _busy ? null : _cancelEdit,
                  ),
                ),
              ],
            ),
          ],
          if (_busy) ...[
            const SizedBox(height: 10),
            const BusyLine(label: 'Сервер проверяет действие…'),
          ],
          if (_message != null) ...[
            const SizedBox(height: 10),
            _Notice(text: _message!, tone: C.green),
          ],
          if (_error != null) ...[
            const SizedBox(height: 10),
            _Notice(text: _error!, tone: C.red),
          ],
        ],
      );

  void _askClose() {
    setState(() {
      _confirmClose = true;
      _inputAction = null;
      _valueController.clear();
      _message = null;
      _error = null;
    });
  }

  void _openInput(ManualTradeAction action) {
    setState(() {
      _inputAction = action;
      _confirmClose = false;
      _valueController.clear();
      _message = null;
      _error = null;
    });
  }

  void _cancelEdit() {
    setState(() {
      _confirmClose = false;
      _inputAction = null;
      _valueController.clear();
    });
  }

  Future<void> _close() => _submit(
        action: ManualTradeAction.close,
        reason: 'owner confirmed manual close from protected trade',
      );

  Future<void> _returnAuto() => _submit(
        action: ManualTradeAction.returnAuto,
        reason: 'owner returned protected trade to automatic management',
      );

  Future<void> _submitInput() async {
    final action = _inputAction;
    final value = _valueController.text.trim();
    if (action == null || value.isEmpty) {
      setState(() => _error = 'Введите значение.');
      return;
    }
    await _submit(
      action: action,
      reason: action == ManualTradeAction.reduce
          ? 'owner requested manual position reduction'
          : 'owner requested tighter protective stop',
      quantity: action == ManualTradeAction.reduce ? value : null,
      stopPrice: action == ManualTradeAction.tightenStop ? value : null,
    );
  }

  Future<void> _submit({
    required ManualTradeAction action,
    required String reason,
    String? quantity,
    String? stopPrice,
  }) async {
    if (_busy) return;
    final key = 'manual-trade:${widget.ideaId}:${action.wireName}:'
        '${DateTime.now().toUtc().microsecondsSinceEpoch}';
    setState(() {
      _busy = true;
      _message = null;
      _error = null;
    });
    try {
      final result = await _client.request(
        ideaId: widget.ideaId,
        action: action,
        reason: reason,
        idempotencyKey: key,
        quantity: quantity,
        stopPrice: stopPrice,
      );
      if (!mounted) return;
      setState(() {
        _confirmClose = false;
        _inputAction = null;
        _valueController.clear();
        _message = result.status == 'COMPLETED' &&
                action == ManualTradeAction.returnAuto
            ? 'Автоматическое сопровождение возвращено сервером.'
            : 'Команда зафиксирована сервером. Биржа ещё не подтвердила '
                'исполнение.';
      });
    } on ApiException catch (failure) {
      if (!mounted) return;
      setState(() => _error = failure.message);
    } catch (failure) {
      if (!mounted) return;
      setState(() => _error = '$failure');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

class _Notice extends StatelessWidget {
  const _Notice({required this.text, required this.tone});

  final String text;
  final Color tone;

  @override
  Widget build(BuildContext context) => Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
        decoration: BoxDecoration(
          color: tone.withValues(alpha: 0.07),
          border: Border.all(color: tone.withValues(alpha: 0.22)),
          borderRadius: BorderRadius.circular(R.inset),
        ),
        child: Text(
          text,
          style: T.body(11, color: C.textSecondary, height: 1.4),
        ),
      );
}
