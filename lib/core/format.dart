import '../domain/ledger/money.dart';

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

/// Сумма книги: «8 420 600 ₽», «−31 562,08 ₽», «18,4 USDT».
///
/// Копейки печатаются, когда они есть: у крупных сумм нули после запятой —
/// шум, а у мелких потеря копеек означает, что число не сходится с брокером.
String fmtMoney(Money value, {bool forceFraction = false, bool sign = false}) {
  final scale = value.currency.scale;
  final unit = _pow10Int(scale);
  final showFraction = forceFraction || value.minor.abs() % unit != 0;
  final whole = value.minor.abs() ~/ unit;
  final frac = value.minor.abs() % unit;

  final groups = whole.toString().replaceAllMapped(
        RegExp(r'\B(?=(\d{3})+(?!\d))'),
        (_) => ' ',
      );
  final tail = showFraction && scale > 0
      ? ',${frac.toString().padLeft(scale, '0')}'
      : '';
  final prefix = value.minor < 0
      ? '−'
      : sign && value.minor > 0
          ? '+'
          : '';
  final suffix = value.currency.symbol ?? value.currency.code;
  // Пробел перед знаком валюты тоже неразрывный: число и «₽» не должны
  // разъезжаться по строкам.
  return '$prefix$groups$tail\u202F$suffix';
}

/// Количество: «3», «0,25», «1 200».
String fmtQuantity(Quantity value) {
  final text = value.toString();
  final dot = text.indexOf(',');
  final whole = dot < 0 ? text : text.substring(0, dot);
  final rest = dot < 0 ? '' : text.substring(dot);
  final negative = whole.startsWith('−');
  final digits = negative ? whole.substring(1) : whole;
  final grouped = digits.replaceAllMapped(
    RegExp(r'\B(?=(\d{3})+(?!\d))'),
    (_) => ' ',
  );
  return '${negative ? '−' : ''}$grouped$rest';
}

int _pow10Int(int n) {
  var result = 1;
  for (var i = 0; i < n; i++) {
    result *= 10;
  }
  return result;
}
