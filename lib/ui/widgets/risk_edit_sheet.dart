import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../core/format.dart';
import '../../domain/models/settings.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'common.dart';

/// Что редактируем в риск-профиле.
enum RiskField { deposit, riskPercent }

/// Границы из макета (панель Tweaks): депозит 100 тыс — 100 млн,
/// риск на сделку 0,25–2% с шагом 0,25.
const _depositMin = 100000.0;
const _depositMax = 100000000.0;
const _riskMin = 0.25;
const _riskMax = 2.0;
const _riskStep = 0.25;

/// Редактирование депозита или процента риска.
///
/// Меняет только политику риска. Реальные остатки и ГО приходят с сервера из
/// брокерского API и руками не задаются (ТЗ §6.1).
Future<void> showRiskEditSheet(
  BuildContext context, {
  required RiskProfile risk,
  required RiskField field,
  required ValueChanged<double> onSubmit,
}) =>
    showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0x00000000),
      barrierColor: const Color(0x99000000),
      isScrollControlled: true,
      builder: (context) => _RiskEditSheet(risk: risk, field: field, onSubmit: onSubmit),
    );

class _RiskEditSheet extends StatefulWidget {
  const _RiskEditSheet({required this.risk, required this.field, required this.onSubmit});

  final RiskProfile risk;
  final RiskField field;
  final ValueChanged<double> onSubmit;

  @override
  State<_RiskEditSheet> createState() => _RiskEditSheetState();
}

class _RiskEditSheetState extends State<_RiskEditSheet> {
  late final TextEditingController _deposit = TextEditingController(
    text: widget.risk.deposit.round().toString(),
  );
  late double _riskPercent = widget.risk.riskPercent;

  @override
  void dispose() {
    _deposit.dispose();
    super.dispose();
  }

  bool get _isDeposit => widget.field == RiskField.deposit;

  double? get _depositValue {
    final parsed = double.tryParse(_deposit.text.replaceAll(RegExp(r'[^0-9]'), ''));
    if (parsed == null || parsed < _depositMin || parsed > _depositMax) return null;
    return parsed;
  }

  void _submit() {
    final value = _isDeposit ? _depositValue : _riskPercent;
    if (value == null) return;
    widget.onSubmit(value);
    Navigator.of(context).pop();
  }

  @override
  Widget build(BuildContext context) {
    final riskRub = _isDeposit
        ? (_depositValue ?? widget.risk.deposit) * widget.risk.riskPercent / 100
        : widget.risk.deposit * _riskPercent / 100;

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
            Text(
              _isDeposit ? 'Депозит' : 'Риск на сделку',
              style: T.jost(18),
            ),
            const SizedBox(height: 4),
            Text(
              _isDeposit
                  ? 'Используется для расчёта объёма позиций'
                  : 'Доля депозита, которой рискуем в одной сделке',
              style: T.body(11, color: C.muted),
            ),
            const SizedBox(height: 14),
            if (_isDeposit) _depositInput() else _riskStepper(),
            const SizedBox(height: 12),
            InsetBox(
              padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 10),
              child: Row(
                children: [
                  Expanded(
                    child: Text('Риск на сделку', style: T.body(12, color: C.muted)),
                  ),
                  Text('${fmt(riskRub.roundToDouble(), 0)} ₽',
                      style: T.mono(12, weight: 600)),
                ],
              ),
            ),
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: Pressable(
                    pressedScale: .98,
                    onTap: _submit,
                    child: Container(
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color: C.accent,
                        borderRadius: BorderRadius.circular(R.button),
                      ),
                      child: Center(
                        child: Text(
                          'Сохранить',
                          style: T.body(14, weight: 800, color: C.onAccent),
                        ),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 9),
                Pressable(
                  onTap: () => Navigator.of(context).pop(),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
                    decoration: BoxDecoration(
                      color: C.inset,
                      border: Border.all(color: C.border),
                      borderRadius: BorderRadius.circular(R.button),
                    ),
                    child: Text('Отмена', style: T.body(14, weight: 700, color: C.muted)),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _depositInput() => Container(
        padding: const EdgeInsets.symmetric(horizontal: 13, vertical: 4),
        decoration: BoxDecoration(
          color: C.inset,
          border: Border.all(color: C.border),
          borderRadius: BorderRadius.circular(R.inner),
        ),
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _deposit,
                autofocus: true,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                style: T.mono(16, weight: 600),
                cursorColor: C.accent,
                decoration: const InputDecoration(
                  border: InputBorder.none,
                  isDense: true,
                  contentPadding: EdgeInsets.symmetric(vertical: 12),
                ),
                onChanged: (_) => setState(() {}),
                onSubmitted: (_) => _submit(),
              ),
            ),
            Text('₽', style: T.mono(14, color: C.muted)),
          ],
        ),
      );

  Widget _riskStepper() => Row(
        children: [
          _stepButton('−', () {
            if (_riskPercent > _riskMin) {
              setState(() => _riskPercent = (_riskPercent - _riskStep).clamp(_riskMin, _riskMax));
            }
          }),
          Expanded(
            child: Center(
              child: Text(
                riskPercentLabel(_riskPercent),
                style: T.mono(20, weight: 600, color: C.accent),
              ),
            ),
          ),
          _stepButton('+', () {
            if (_riskPercent < _riskMax) {
              setState(() => _riskPercent = (_riskPercent + _riskStep).clamp(_riskMin, _riskMax));
            }
          }),
        ],
      );

  Widget _stepButton(String label, VoidCallback onTap) => Pressable(
        onTap: onTap,
        child: Container(
          width: 52,
          height: 44,
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: C.inset,
            border: Border.all(color: C.border),
            borderRadius: BorderRadius.circular(R.inner),
          ),
          child: Text(label, style: T.body(18, weight: 700, color: C.text)),
        ),
      );
}
