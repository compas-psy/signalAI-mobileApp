import 'package:flutter/material.dart';

import '../../state/risk_on_controller.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Owner control for a bounded per-idea RISK ON override.
///
/// Venue/account are the only editable inputs. The phone cannot enter or
/// calculate risk %, quantity or leverage: those values are shown only after a
/// server preview and are persisted only after a second explicit confirmation.
class RiskOnPanel extends StatefulWidget {
  const RiskOnPanel({
    super.key,
    this.ideaId = '',
    this.controller,
    this.initialVenue = '',
    this.initialAccount = '',
  }) : assert(controller != null || ideaId != '');

  final String ideaId;
  final RiskOnController? controller;
  final String initialVenue;
  final String initialAccount;

  @override
  State<RiskOnPanel> createState() => _RiskOnPanelState();
}

class _RiskOnPanelState extends State<RiskOnPanel> {
  late RiskOnController _controller;
  late bool _ownsController;
  late final TextEditingController _venue;
  late final TextEditingController _account;

  @override
  void initState() {
    super.initState();
    _bindController();
    _venue = TextEditingController(text: widget.initialVenue);
    _account = TextEditingController(text: widget.initialAccount);
  }

  @override
  void didUpdateWidget(RiskOnPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    final injectedChanged = oldWidget.controller != widget.controller;
    final ownedIdeaChanged = widget.controller == null &&
        oldWidget.ideaId != widget.ideaId;
    if (injectedChanged || ownedIdeaChanged) {
      if (_ownsController) _controller.dispose();
      _bindController();
    }
    if (oldWidget.initialVenue != widget.initialVenue &&
        _venue.text == oldWidget.initialVenue) {
      _venue.text = widget.initialVenue;
    }
    if (oldWidget.initialAccount != widget.initialAccount &&
        _account.text == oldWidget.initialAccount) {
      _account.text = widget.initialAccount;
    }
  }

  void _bindController() {
    _ownsController = widget.controller == null;
    _controller = widget.controller ?? RiskOnController(ideaId: widget.ideaId);
  }

  @override
  void dispose() {
    if (_ownsController) _controller.dispose();
    _venue.dispose();
    _account.dispose();
    super.dispose();
  }

  Future<void> _preview() async {
    try {
      await _controller.preview(venue: _venue.text, account: _account.text);
    } catch (_) {
      // Controller exposes the error in the panel. Swallow here so an async
      // button callback does not escape into Flutter's global error boundary.
    }
  }

  Future<void> _confirm() async {
    try {
      await _controller.confirm();
    } catch (_) {
      // Same rule as preview: visible error, no unhandled UI exception.
    }
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
        animation: _controller,
        builder: (context, _) {
          final preview = _controller.previewData;
          final result = _controller.result;
          return SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('RISK ON · ручное усиление'),
                const SizedBox(height: 7),
                Text(
                  'Вы выбираете только площадку и счёт. Риск и объём считает '
                  'сервер в пределах жёстких лимитов.',
                  style: T.body(10.5, color: C.muted, height: 1.4),
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Expanded(
                      child: _ScopeField(
                        controller: _venue,
                        label: 'Площадка',
                        hint: 'TINVEST / BYBIT / LIGHTER',
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: _ScopeField(
                        controller: _account,
                        label: 'Счёт',
                        hint: 'server account id',
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 9),
                ActionButton(
                  label: _controller.loading ? 'Проверяю…' : 'Рискнуть',
                  primary: true,
                  dense: true,
                  color: C.warning,
                  onTap: _controller.loading ? null : _preview,
                ),
                if (_controller.error != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    _controller.error!,
                    style: T.body(10.5, color: C.warning, height: 1.4),
                  ),
                ],
                if (preview != null) ...[
                  const SizedBox(height: 10),
                  _RiskOnPreviewBox(
                    preview: preview,
                    onConfirm: preview.allowed && !_controller.loading
                        ? _confirm
                        : null,
                    onCancel: _controller.clear,
                  ),
                ],
                if (result != null) ...[
                  const SizedBox(height: 10),
                  _RiskOnResultBox(result: result),
                ],
              ],
            ),
          );
        },
      );
}

class _ScopeField extends StatelessWidget {
  const _ScopeField({
    required this.controller,
    required this.label,
    required this.hint,
  });

  final TextEditingController controller;
  final String label;
  final String hint;

  @override
  Widget build(BuildContext context) => TextField(
        controller: controller,
        autocorrect: false,
        enableSuggestions: false,
        textCapitalization: TextCapitalization.characters,
        style: T.mono(11, color: C.text),
        cursorColor: C.accent,
        decoration: InputDecoration(
          isDense: true,
          labelText: label,
          hintText: hint,
          labelStyle: T.body(10, color: C.muted),
          hintStyle: T.mono(9.5, color: C.dim),
          filled: true,
          fillColor: C.inset,
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(R.inset),
            borderSide: const BorderSide(color: C.border),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(R.inset),
            borderSide: const BorderSide(color: C.accent),
          ),
        ),
      );
}

class _RiskOnPreviewBox extends StatelessWidget {
  const _RiskOnPreviewBox({
    required this.preview,
    required this.onConfirm,
    required this.onCancel,
  });

  final RiskOnPreview preview;
  final Future<void> Function()? onConfirm;
  final VoidCallback onCancel;

  @override
  Widget build(BuildContext context) => InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Серверный preview · ${preview.venue} / ${preview.account}',
              style: T.mono(10.5, color: C.text),
            ),
            const SizedBox(height: 7),
            KeyValueRow(
              name: 'Риск',
              value:
                  '${_percent(preview.baseRiskPct)} → ${_percent(preview.effectiveRiskPct)}',
            ),
            KeyValueRow(
              name: 'Объём',
              value: '${_number(preview.baseQuantity)} → ${_number(preview.effectiveQuantity)}',
            ),
            KeyValueRow(
              name: 'Риск, деньги',
              value: _number(preview.effectiveRiskAmount),
            ),
            KeyValueRow(
              name: 'Жёсткий потолок риска',
              value: _percent(preview.hardCapRiskPct),
            ),
            KeyValueRow(name: 'Ограничивает', value: preview.bindingLimit),
            const SizedBox(height: 5),
            Text(
              preview.effectiveLeverage == null
                  ? 'Плечо не увеличивается автоматически · потолок ${_number(preview.hardCapLeverage)}×'
                  : 'Плечо ${_number(preview.effectiveLeverage!)}× · потолок ${_number(preview.hardCapLeverage)}×',
              style: T.body(10.5, color: C.muted, height: 1.35),
            ),
            if (preview.blockers.isNotEmpty) ...[
              const SizedBox(height: 7),
              for (final blocker in preview.blockers)
                Text(
                  '• $blocker',
                  style: T.body(10.5, color: C.warning, height: 1.35),
                ),
            ],
            if (onConfirm != null) ...[
              const SizedBox(height: 9),
              ActionButton(
                label: 'Подтвердить Рискнуть',
                primary: true,
                dense: true,
                color: C.warning,
                onTap: onConfirm,
              ),
            ],
            const SizedBox(height: 6),
            ActionButton(label: 'Отмена', dense: true, onTap: onCancel),
          ],
        ),
      );
}

class _RiskOnResultBox extends StatelessWidget {
  const _RiskOnResultBox({required this.result});

  final RiskOnResult result;

  @override
  Widget build(BuildContext context) => InsetBox(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'RISK ON подтверждён',
              style: T.body(11, weight: 800, color: C.warning),
            ),
            const SizedBox(height: 6),
            KeyValueRow(name: 'Override', value: result.riskOverrideId),
            KeyValueRow(
              name: 'Риск',
              value: _percent(result.effectiveRiskPct),
            ),
            KeyValueRow(name: 'Объём', value: _number(result.effectiveQuantity)),
            Text(
              'Сохранён серверный override; повтор того же подтверждения идемпотентен.',
              style: T.body(10, color: C.muted, height: 1.35),
            ),
          ],
        ),
      );
}

String _percent(String raw) {
  final value = double.tryParse(raw.replaceAll(',', '.'));
  if (value == null) return raw;
  return '${(value * 100).toStringAsFixed(2).replaceAll('.', ',')}%';
}

String _number(String raw) {
  final value = double.tryParse(raw.replaceAll(',', '.'));
  if (value == null) return raw;
  if (value == value.truncateToDouble()) return value.toInt().toString();
  return value.toStringAsFixed(4).replaceFirst(RegExp(r'0+$'), '').replaceFirst(RegExp(r'\.$'), '').replaceAll('.', ',');
}
