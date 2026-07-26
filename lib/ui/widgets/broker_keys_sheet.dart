import 'package:flutter/material.dart';

import '../../domain/broker/broker.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Ввод ключей биржи.
///
/// Ключи уходят в защищённое хранилище на устройстве и обратно не читаются:
/// поля всегда пустые, потому что показать сохранённый секрет невозможно —
/// и это не недоработка, а условие хранения.
Future<void> showBrokerKeysSheet(
  BuildContext context, {
  required TradingMode mode,
  required Future<void> Function(String apiKey, String apiSecret) onSubmit,
}) =>
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0x00000000),
      barrierColor: const Color(0x99000000),
      isScrollControlled: true,
      builder: (context) => _BrokerKeysSheet(mode: mode, onSubmit: onSubmit),
    );

class _BrokerKeysSheet extends StatefulWidget {
  const _BrokerKeysSheet({required this.mode, required this.onSubmit});

  final TradingMode mode;
  final Future<void> Function(String apiKey, String apiSecret) onSubmit;

  @override
  State<_BrokerKeysSheet> createState() => _BrokerKeysSheetState();
}

class _BrokerKeysSheetState extends State<_BrokerKeysSheet> {
  final _key = TextEditingController();
  final _secret = TextEditingController();
  bool _busy = false;

  @override
  void dispose() {
    _key.dispose();
    _secret.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_busy) return;
    setState(() => _busy = true);
    final navigator = Navigator.of(context);
    await widget.onSubmit(_key.text, _secret.text);
    if (mounted) navigator.pop();
  }

  @override
  Widget build(BuildContext context) {
    final live = widget.mode == TradingMode.live;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Container(
        decoration: const BoxDecoration(
          color: C.sheet,
          borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
        ),
        padding: const EdgeInsets.fromLTRB(18, 10, 18, 22),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Center(
              child: Container(
                width: 38,
                height: 4,
                decoration: BoxDecoration(
                  color: C.handle,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            const SizedBox(height: 16),
            Text('Ключи Bybit', style: T.body(16, weight: 700)),
            const SizedBox(height: 4),
            Text(
              live
                  ? 'Живой счёт: ключ распоряжается реальными деньгами. Права на '
                      'вывод средств давать не нужно — приложению хватает торговли.'
                  : 'Тренировочный счёт testnet: денег там нет, ошибиться безопасно.',
              style: T.body(11.5, color: live ? C.red : C.muted, height: 1.4),
            ),
            const SizedBox(height: 14),
            _Field(label: 'API key', controller: _key),
            const SizedBox(height: 10),
            _Field(label: 'API secret', controller: _secret, obscure: true),
            const SizedBox(height: 8),
            Text(
              'Ключи шифруются Android Keystore. Секрет не покидает устройство '
              'и не показывается обратно — подпись запросов считается на месте.',
              style: T.body(10.5, color: C.muted, height: 1.4),
            ),
            const SizedBox(height: 16),
            Pressable(
              onTap: _busy ? null : _submit,
              child: Container(
                height: 46,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: _busy ? C.chip : C.accent,
                  borderRadius: BorderRadius.circular(12),
                ),
                child: Text(
                  _busy ? 'Проверяю на бирже…' : 'Сохранить и проверить',
                  style: T.body(13.5, weight: 700, color: _busy ? C.muted : C.bg),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Field extends StatelessWidget {
  const _Field({required this.label, required this.controller, this.obscure = false});

  final String label;
  final TextEditingController controller;
  final bool obscure;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(label.toUpperCase(), style: T.sectionLabel(color: C.muted)),
          const SizedBox(height: 6),
          Container(
            decoration: BoxDecoration(
              color: C.inset,
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: C.border),
            ),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
            child: TextField(
              controller: controller,
              obscureText: obscure,
              autocorrect: false,
              enableSuggestions: false,
              style: T.mono(13, color: C.text),
              cursorColor: C.accent,
              decoration: const InputDecoration(border: InputBorder.none),
            ),
          ),
        ],
      );
}
