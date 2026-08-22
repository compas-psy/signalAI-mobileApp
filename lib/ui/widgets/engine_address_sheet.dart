import 'package:flutter/material.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Ввод адреса движка (§18).
///
/// Только HTTPS: по этому адресу приходят торговые идеи и уходит
/// подтверждение сделки. `ApiClient` всё равно откажется работать по HTTP —
/// но отказ в момент ввода понятнее, чем «нет связи с сервером» через час.
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
      builder: (context) => _EngineAddressSheet(
          current: current, currentToken: currentToken, onSubmit: onSubmit),
    );

class _EngineAddressSheet extends StatefulWidget {
  const _EngineAddressSheet({
    required this.current,
    required this.currentToken,
    required this.onSubmit,
  });

  final String current;
  final String currentToken;
  final void Function(String url, String token, String pairingSessionId) onSubmit;

  @override
  State<_EngineAddressSheet> createState() => _EngineAddressSheetState();
}

class _EngineAddressSheetState extends State<_EngineAddressSheet> {
  late final TextEditingController _field =
      TextEditingController(text: widget.current);
  late final TextEditingController _token =
      TextEditingController(text: widget.currentToken);
  final TextEditingController _pairingSession = TextEditingController();

  @override
  void dispose() {
    _field.dispose();
    _token.dispose();
    _pairingSession.dispose();
    super.dispose();
  }

  /// Почему адрес нельзя принять. null — можно.
  String? get _problem {
    final value = _field.text.trim();
    // Пустое поле — осознанный сброс к адресу из сборки, а не ошибка.
    if (value.isEmpty) return null;
    final uri = Uri.tryParse(value);
    if (uri == null || !uri.hasAuthority) return 'Это не похоже на адрес.';
    if (uri.scheme != 'https') {
      return 'Только https: по этому адресу уходит подтверждение сделки.';
    }
    if (value.endsWith('/')) {
      return 'Без косой черты в конце — пути движок дописывает сам.';
    }
    return null;
  }

  void _submit() {
    if (_problem != null) return;
    widget.onSubmit(
      _field.text.trim(),
      _token.text.trim(),
      _pairingSession.text.trim(),
    );
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
            Text('Адрес движка', style: T.jost(18)),
            const SizedBox(height: 4),
            Text(
              'Сервер, который считает идеи. Ключи бирж сюда не относятся: '
              'они дают котировки и счёт.',
              style: T.body(11, color: C.muted, height: 1.45),
            ),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: C.inset,
                border: Border.all(color: problem == null ? C.border : C.redBorder),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: TextField(
                controller: _field,
                autofocus: true,
                keyboardType: TextInputType.url,
                autocorrect: false,
                style: T.mono(13, weight: 600),
                cursorColor: C.accent,
                onChanged: (_) => setState(() {}),
                onSubmitted: (_) => _submit(),
                decoration: InputDecoration(
                  border: InputBorder.none,
                  hintText: 'https://engine.example.ru',
                  hintStyle: T.mono(13, color: C.dim),
                ),
              ),
            ),
            const SizedBox(height: 10),
            // Bootstrap-пароль используется ровно один раз: сервер выдаёт
            // отдельный токен конкретного устройства и исходный не хранится.
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: C.inset,
                border: Border.all(color: C.border),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: TextField(
                controller: _token,
                autocorrect: false,
                obscureText: true,
                style: T.mono(13, weight: 600),
                cursorColor: C.accent,
                onSubmitted: (_) => _submit(),
                decoration: InputDecoration(
                  border: InputBorder.none,
                  hintText: 'одноразовый токен привязки',
                  hintStyle: T.mono(12, color: C.dim),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              decoration: BoxDecoration(
                color: C.inset,
                border: Border.all(color: C.border),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: TextField(
                controller: _pairingSession,
                autocorrect: false,
                obscureText: true,
                style: T.mono(13, weight: 600),
                cursorColor: C.accent,
                onSubmitted: (_) => _submit(),
                decoration: InputDecoration(
                  border: InputBorder.none,
                  hintText: 'одноразовая сессия привязки',
                  hintStyle: T.mono(12, color: C.dim),
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              problem ??
                  (empty
                      ? 'Пусто — вернёмся к адресу из сборки.'
                      : 'Bootstrap-токен и отдельная сессия обменяются на токен '
                          'устройства в Android Keystore; пустой bootstrap отзовёт привязку.'),
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
                    label: 'Сохранить',
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
