/// Форматирование чисел ровно как в макете:
/// разряды разделяются узким неразрывным пробелом (U+202F),
/// дробная часть — запятой, минус — типографским U+2212.
String fmt(num n, int decimals) {
  final s = n.abs().toStringAsFixed(decimals);
  final parts = s.split('.');
  final intPart = parts[0].replaceAllMapped(
    RegExp(r'\B(?=(\d{3})+(?!\d))'),
    (_) => ' ',
  );
  final tail = parts.length > 1 ? ',${parts[1]}' : '';
  return '${n < 0 ? '−' : ''}$intPart$tail';
}

/// Цена инструмента с нужным числом знаков после запятой.
String fmtPrice(num value, int decimals) => fmt(value, decimals);

/// Дробное число с запятой вместо точки («0.75» → «0,75»).
String comma(num n) {
  final s = n == n.roundToDouble() && n.abs() < 1e15
      ? (n % 1 == 0 && n is! int ? n.toStringAsFixed(n.abs() < 1 ? 2 : 0) : n.toString())
      : n.toString();
  return s.replaceAll('.', ',');
}

/// Проценты риска: 0.75 → «0,75%», 1 → «1%».
String riskPercentLabel(double pct) {
  final s = pct == pct.roundToDouble() ? pct.toStringAsFixed(0) : pct.toString();
  return '${s.replaceAll('.', ',')}%';
}

/// «+2,3R» / «−1,0R» — результат сделки в единицах риска.
String rMultiple(double r) {
  final sign = r < 0 ? '−' : '+';
  return '$sign${r.abs().toStringAsFixed(1).replaceAll('.', ',')}R';
}
