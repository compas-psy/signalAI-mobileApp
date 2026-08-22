import 'package:flutter/material.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Адрес движка и редкая первичная привязка устройства.
///
/// Пользователь видит один короткоживущий owner-code из Telegram. После
/// успешного обмена active-device bearer остаётся только в Android Keystore.
Future<void> showEngineAddressSheet(
  BuildContext context, {
  required String current,
  required String currentToken,
  required void Function(String url, String token, String pairingSessionId) onSubmit,
}) =>
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0x00000000),
      barrierColor: const Color(0x99000000),
      isScrollControlled: true,
      builder: (context) => _EngineAddressSheet(current: current, onSubmit: onSubmit),
    );

class _EngineAddressSheet extends StatefulWidget {
  const _EngineAddressSheet({
    required this.current,
    required this.onSubmit,
  });

  final String current;
  final void Function(String url, String token, String pairingSessionId) onSubmit;

  @override
  State<_EngineAddressSheet> createState() => _EngineAddressSheetState();
}

class _EngineAddressSheetState extends State<_EngineAddressSheet> {
  late final TextEditingController _field =
      TextEditingController(text: widget.current);
  final TextEditingController _pairingCode = TextEditingController();

  @override
  void dispose() {
    _field.dispose();
    _pairingCode.dispose();
    super.dispose();
  }

  String? get _problem {
    final value = _field.text.trim();
    if (value.isEmpty) return null;
    final uri = Uri.tryParse(value);
    if (uri == null || !uri.hasAuthority) return 'Это не похоже на адрес.';
    if (uri.scheme != 'https') {
      return 'Только https: по этому адресу уходит подтверждение сделки.';
    }
    if (value.endsWith('/')) {
      return 'Без косой черты в конце — пути движок дописывает сам.';
    }
    final code = _pairingCode.text.trim();
    if (!RegExp(r'^[A-Za-z0-9_-]{43,128}$').hasMatch(code)) {
      return 'Вставьте одноразовый код привязки из Telegram.';
    }
    return null;
  }

  void _submit() {
    if (_problem != null) return;
    final code = _pairingCode.text.trim();
    // Controller keeps the legacy two-argument transport boundary during this
    // hotfix. Both values are the same ephemeral code; the server authorizes
    // only X-Pairing-Session-Id and never treats it as a business bearer.
    widget.onSubmit(_field.text.trim(), code, code);
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final problem = _problem;
    final empty = _field.text.trim().isEmpty;
    return Padding(
      padding: EdgeInsets.only(bottom: MediaQuery.of(context).viewInsets.bottom),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 22),
        decoration: const BoxDecoration(
          color: C.sheet,
          border: Border(top: BorderSide(color: C.borderStrong)),
          borderRadius: BorderRadius.vertical(top: Radius.circular(R.sheet)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                width: 36,
                height: 4,
                margin: const EdgeInsets.only(bottom: 14),
                decoration: BoxDecoration(
                  color: C.handle,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
            Text('Подключение SignalAI', style: T.jost(18)),
            const SizedBox(height: 4),
            Text(
              'Адрес сервера обычно менять не нужно. Для нового телефона или '
              'переустановки нужен один одноразовый код из Telegram.',
              style: T.body(11, color: C.muted, height: 1.45),
            ),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: C.inset,
                border: Border.all(color: C.border),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: TextField(
                controller: _field,
                keyboardType: TextInputType.url,
                autocorrect: false,
                style: T.mono(13, weight: 600),
                cursorColor: C.accent,
                onChanged: (_) => setState(() {}),
                decoration: InputDecoration(
                  border: InputBorder.none,
                  hintText: 'https://engine.example.ru',
                  hintStyle: T.mono(13, color: C.dim),
                ),
              ),
            ),
            const SizedBox(height: 10),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: C.inset,
                border: Border.all(color: problem == null ? C.border : C.redBorder),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: TextField(
                controller: _pairingCode,
                autofocus: true,
                autocorrect: false,
                obscureText: true,
                style: T.mono(13, weight: 600),
                cursorColor: C.accent,
                onChanged: (_) => setState(() {}),
                onSubmitted: (_) => _submit(),
                decoration: InputDecoration(
                  border: InputBorder.none,
                  hintText: 'код привязки из Telegram',
                  hintStyle: T.mono(12, color: C.dim),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              problem ??
                  (empty
                      ? 'Пустой адрес отключает привязку к движку.'
                      : 'Код действует 15 минут и один раз. После привязки '
                          'технические коды больше не понадобятся.'),
              style: T.body(11, color: problem == null ? C.faint : C.red, height: 1.4),
            ),
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: ActionButton(
                    label: 'Отмена',
                    onTap: () => Navigator.of(context).pop(),
                  ),
                ),
                const SizedBox(width: 9),
                Expanded(
                  child: ActionButton(
                    label: 'Привязать',
                    primary: true,
                    onTap: problem == null ? _submit : null,
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
