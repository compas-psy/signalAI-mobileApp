from pathlib import Path

path = Path('lib/ui/screens/server_control_screen.dart')
text = path.read_text()

replacements = [
    (
        "'PF ${_n(run.profitFactor)} · expectancy ${_r(run.expectancyR)} · MaxDD ${_n(run.maxDrawdown)}',",
        "_backtestMetricText(run),",
    ),
    (
        "    final isR = metricSpace == 'R_MULTIPLES';",
        "    final isR = metricSpace == 'R_MULTIPLES';\n    final isCarry = metricSpace == 'CARRY_BPS';",
    ),
    (
        "'N ${run.trades} · E[R] ${_signedR(run.expectancyR)} · PF ${_dotN(run.profitFactor)} · MaxDD ${isR ? _rMagnitude(run.maxDrawdown) : _dotN(run.maxDrawdown)}',",
        "'N ${run.trades} · ${_backtestMetricText(run)}',",
    ),
    (
        "          if (isR) ...[\n            const SizedBox(height: 4),\n            Text(\n              'R_MULTIPLES · account return не моделируется',\n              style: T.body(9.6, color: C.dim),\n            ),\n          ],",
        "          if (isR) ...[\n            const SizedBox(height: 4),\n            Text(\n              'R_MULTIPLES · account return не моделируется',\n              style: T.body(9.6, color: C.dim),\n            ),\n          ] else if (isCarry) ...[\n            const SizedBox(height: 4),\n            Text(\n              'CARRY_BPS · funding+basis+cost outcome · не сравнивается напрямую с R',\n              style: T.body(9.6, color: C.dim),\n            ),\n          ],",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'Expected exactly one match, got {count}: {old[:80]}')
    text = text.replace(old, new, 1)

helper_anchor = "String _rMagnitude(double? value) =>\n    value == null ? '—' : '${value.toStringAsFixed(2)}R';\n"
helper_block = r'''

Map<String, dynamic> _backtestOos(BacktestRunSummary run) {
  final raw = run.report['oos'];
  if (raw is Map<String, dynamic>) return raw;
  if (raw is Map) {
    return raw.map((key, value) => MapEntry(key.toString(), value));
  }
  return const <String, dynamic>{};
}

double? _backtestNumber(Object? value) {
  if (value is num) return value.toDouble();
  if (value == null) return null;
  return double.tryParse(value.toString());
}

String _signedBps(double? value) {
  if (value == null) return '—';
  final sign = value > 0 ? '+' : '';
  return '$sign${value.toStringAsFixed(2)} bps';
}

String _bpsMagnitude(double? value) =>
    value == null ? '—' : '${value.toStringAsFixed(2)} bps';

String _backtestProfitFactor(BacktestRunSummary run) {
  if (run.profitFactor != null) return _dotN(run.profitFactor);
  final raw = _backtestOos(run)['profit_factor'];
  if (raw == null) return '—';
  final text = raw.toString().trim();
  if (text.toUpperCase() == 'INF') return 'INF';
  return _dotN(_backtestNumber(raw));
}

String _backtestMetricText(BacktestRunSummary run) {
  final metricSpace = run.report['metric_space']?.toString();
  if (metricSpace == 'CARRY_BPS') {
    final oos = _backtestOos(run);
    return 'E[carry] ${_signedBps(_backtestNumber(oos['expectancy_bps']))} · '
        'PF ${_backtestProfitFactor(run)} · '
        'MaxDD ${_bpsMagnitude(_backtestNumber(oos['max_drawdown_bps']))}';
  }
  return 'E[R] ${_signedR(run.expectancyR)} · '
      'PF ${_dotN(run.profitFactor)} · MaxDD ${_rMagnitude(run.maxDrawdown)}';
}
'''
if text.count(helper_anchor) != 1:
    raise SystemExit('Helper anchor not unique')
text = text.replace(helper_anchor, helper_anchor + helper_block, 1)
path.write_text(text)
