/// Деньги и количества в фиксированной точке.
///
/// Почему не `double`. `0.1 + 0.2` в двоичной плавающей точке даёт
/// `0.30000000000000004`; на тысяче операций книги такая ошибка накапливается
/// и баланс перестаёт сходиться с брокером. Здесь всё хранится целым числом
/// минимальных единиц (копеек, сатоши) — сложение и вычитание точны по
/// определению, а деление выполняется явно и с явным правилом округления.
///
/// Внешнего пакета `decimal` нет намеренно: нужны только сложение, вычитание,
/// умножение на количество и деление с округлением — это тридцать строк,
/// которые полностью покрыты тестами, против ещё одной зависимости в
/// приложении, подтверждающем сделки.
library;

/// Валюта учёта: код и число знаков после запятой.
class Currency {
  const Currency(this.code, this.scale, {this.symbol});

  /// ISO-код или тикер: RUB, USD, USDT, BTC.
  final String code;

  /// Знаков после запятой: 2 для рубля, 8 для биткоина.
  final int scale;

  /// Символ для вывода. Нет — печатается код.
  final String? symbol;

  static const rub = Currency('RUB', 2, symbol: '₽');
  static const usd = Currency('USD', 2, symbol: r'$');
  static const usdt = Currency('USDT', 2);
  static const btc = Currency('BTC', 8);

  static const _known = [rub, usd, usdt, btc];

  /// Валюта по коду. Незнакомая получает две цифры — так ведёт себя
  /// большинство фиатных и стейбл-валют, и это честнее, чем падать.
  static Currency parse(String code) => _known.firstWhere(
        (c) => c.code.toUpperCase() == code.toUpperCase(),
        orElse: () => Currency(code.toUpperCase(), 2),
      );

  @override
  bool operator ==(Object other) =>
      other is Currency && other.code == code && other.scale == scale;

  @override
  int get hashCode => Object.hash(code, scale);

  @override
  String toString() => code;
}

/// Правило округления при делении.
enum Rounding {
  /// К ближайшему, половину — от нуля. Обычное денежное округление.
  half,

  /// К нулю: так брокер считает количество лотов.
  down,

  /// От нуля: так считают требуемое обеспечение — в пользу осторожности.
  up,
}

/// Сумма в конкретной валюте.
///
/// Складывать можно только одинаковые валюты: `100 ₽ + 5 $` — не сумма, а
/// ошибка модели, и она должна падать здесь, а не превращаться в 105 чего-то.
class Money implements Comparable<Money> {
  const Money(this.minor, this.currency);

  /// Значение в минимальных единицах: копейки для рубля.
  final int minor;

  final Currency currency;

  static Money zero(Currency currency) => Money(0, currency);

  /// Из «человеческого» числа: `Money.of(1234.56, Currency.rub)`.
  ///
  /// Единственное место, где double допустим, — граница с внешними данными
  /// (ответ брокера, ISS). Дальше внутрь приложения он не проходит.
  factory Money.of(num amount, Currency currency) =>
      Money(_roundToInt(amount * _pow10(currency.scale)), currency);

  /// Разбор строки «1234.56» или «1 234,56» без потери точности.
  factory Money.parse(String raw, Currency currency) {
    final cleaned = raw
        .replaceAll('−', '-')
        .replaceAll(' ', '')
        .replaceAll(' ', '')
        .replaceAll(',', '.');
    final negative = cleaned.startsWith('-');
    final body = negative ? cleaned.substring(1) : cleaned;
    final dot = body.indexOf('.');
    final whole = dot < 0 ? body : body.substring(0, dot);
    var frac = dot < 0 ? '' : body.substring(dot + 1);
    if (frac.length > currency.scale) {
      // Лишние знаки округляем, а не отбрасываем: брокер может прислать
      // цену с большей точностью, чем валюта учёта.
      final keep = int.parse(frac.substring(0, currency.scale).padRight(currency.scale, '0'));
      final next = int.parse(frac[currency.scale]);
      final value = int.parse(whole.isEmpty ? '0' : whole) * _pow10(currency.scale) +
          keep +
          (next >= 5 ? 1 : 0);
      return Money(negative ? -value : value, currency);
    }
    frac = frac.padRight(currency.scale, '0');
    final value = int.parse(whole.isEmpty ? '0' : whole) * _pow10(currency.scale) +
        (frac.isEmpty ? 0 : int.parse(frac));
    return Money(negative ? -value : value, currency);
  }

  bool get isZero => minor == 0;
  bool get isNegative => minor < 0;
  bool get isPositive => minor > 0;

  Money operator +(Money other) {
    _require(other);
    return Money(minor + other.minor, currency);
  }

  Money operator -(Money other) {
    _require(other);
    return Money(minor - other.minor, currency);
  }

  Money operator -() => Money(-minor, currency);

  Money get abs => Money(minor.abs(), currency);

  /// Умножение на количество: цена × лоты.
  Money multiplyBy(Quantity quantity, {Rounding rounding = Rounding.half}) =>
      Money(_divide(minor * quantity.raw, _pow10(quantity.scale), rounding), currency);

  /// Умножение на коэффициент (доля, множитель контракта).
  Money scaleBy(num factor, {Rounding rounding = Rounding.half}) =>
      Money(_roundToInt(minor * factor, rounding), currency);

  /// Пересчёт в другую валюту по курсу. Курс — сколько [target] за единицу
  /// текущей валюты.
  Money convert(Currency target, num rate, {Rounding rounding = Rounding.half}) {
    final scaled = minor * rate * _pow10(target.scale) / _pow10(currency.scale);
    return Money(_roundToInt(scaled, rounding), target);
  }

  @override
  int compareTo(Money other) {
    _require(other);
    return minor.compareTo(other.minor);
  }

  bool operator <(Money other) => compareTo(other) < 0;
  bool operator <=(Money other) => compareTo(other) <= 0;
  bool operator >(Money other) => compareTo(other) > 0;
  bool operator >=(Money other) => compareTo(other) >= 0;

  void _require(Money other) {
    if (other.currency != currency) {
      throw ArgumentError(
        'Сложение разных валют: ${currency.code} и ${other.currency.code}. '
        'Пересчёт делается явно через convert() с сохранением курса.',
      );
    }
  }

  /// Значение как число — только для графиков и сравнения, не для расчётов.
  double get asDouble => minor / _pow10(currency.scale);

  @override
  bool operator ==(Object other) =>
      other is Money && other.minor == minor && other.currency == currency;

  @override
  int get hashCode => Object.hash(minor, currency);

  @override
  String toString() => '${_digits(minor, currency.scale)} ${currency.code}';

  Map<String, dynamic> toJson() => {'minor': minor, 'cur': currency.code, 'scale': currency.scale};

  static Money fromJson(Map<String, dynamic> json) => Money(
        (json['minor'] as num).toInt(),
        Currency(json['cur'] as String, (json['scale'] as num?)?.toInt() ?? 2),
      );
}

/// Количество инструмента: лоты, контракты, монеты.
///
/// Отдельный тип, а не `Money` без валюты: 3 контракта и 3 рубля нельзя
/// складывать, и система типов должна это знать.
class Quantity implements Comparable<Quantity> {
  const Quantity(this.raw, [this.scale = 8]);

  /// Значение, умноженное на 10^[scale].
  final int raw;

  final int scale;

  static const zero = Quantity(0);

  factory Quantity.of(num value, [int scale = 8]) =>
      Quantity(_roundToInt(value * _pow10(scale)), scale);

  /// Целое количество: контракты, акции.
  factory Quantity.units(int value) => Quantity(value * _pow10(8));

  bool get isZero => raw == 0;
  bool get isNegative => raw < 0;
  bool get isPositive => raw > 0;

  Quantity operator +(Quantity other) => Quantity(raw + _align(other), scale);
  Quantity operator -(Quantity other) => Quantity(raw - _align(other), scale);
  Quantity operator -() => Quantity(-raw, scale);
  Quantity get abs => Quantity(raw.abs(), scale);

  Quantity min(Quantity other) => compareTo(other) <= 0 ? this : other;

  int _align(Quantity other) => other.scale == scale
      ? other.raw
      : _divide(other.raw * _pow10(scale), _pow10(other.scale), Rounding.half);

  @override
  int compareTo(Quantity other) => raw.compareTo(_align(other));

  bool operator <(Quantity other) => compareTo(other) < 0;
  bool operator <=(Quantity other) => compareTo(other) <= 0;
  bool operator >(Quantity other) => compareTo(other) > 0;
  bool operator >=(Quantity other) => compareTo(other) >= 0;

  double get asDouble => raw / _pow10(scale);

  @override
  bool operator ==(Object other) =>
      other is Quantity && other.compareTo(this) == 0 && other.raw.sign == raw.sign;

  @override
  int get hashCode => asDouble.hashCode;

  @override
  String toString() => _digits(raw, scale, trimTrailingZeros: true);

  Map<String, dynamic> toJson() => {'raw': raw, 'scale': scale};

  static Quantity fromJson(Map<String, dynamic> json) =>
      Quantity((json['raw'] as num).toInt(), (json['scale'] as num?)?.toInt() ?? 8);
}

int _pow10(int n) {
  var result = 1;
  for (var i = 0; i < n; i++) {
    result *= 10;
  }
  return result;
}

/// Деление целых с явным правилом округления. Знак учитывается: −5/2 при
/// [Rounding.half] даёт −3, а не −2 — половина уходит от нуля симметрично.
int _divide(int value, int divisor, Rounding rounding) {
  if (divisor == 0) throw ArgumentError('Деление на ноль');
  final negative = (value < 0) != (divisor < 0);
  final a = value.abs();
  final b = divisor.abs();
  final quotient = a ~/ b;
  final remainder = a % b;
  final bump = switch (rounding) {
    Rounding.half => remainder * 2 >= b ? 1 : 0,
    Rounding.down => 0,
    Rounding.up => remainder > 0 ? 1 : 0,
  };
  final result = quotient + bump;
  return negative ? -result : result;
}

int _roundToInt(num value, [Rounding rounding = Rounding.half]) {
  if (value is int) return value;
  return switch (rounding) {
    Rounding.half => value.abs().round() * (value.isNegative ? -1 : 1),
    Rounding.down => value.truncate(),
    Rounding.up => value.abs().ceil() * (value.isNegative ? -1 : 1),
  };
}

String _digits(int raw, int scale, {bool trimTrailingZeros = false}) {
  final negative = raw < 0;
  final digits = raw.abs().toString().padLeft(scale + 1, '0');
  final whole = digits.substring(0, digits.length - scale);
  var frac = scale == 0 ? '' : digits.substring(digits.length - scale);
  if (trimTrailingZeros) {
    frac = frac.replaceFirst(RegExp(r'0+$'), '');
  }
  final body = frac.isEmpty ? whole : '$whole,$frac';
  return negative ? '−$body' : body;
}
