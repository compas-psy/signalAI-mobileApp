import 'ledger_event.dart';
import 'money.dart';

/// Тип счёта. От него зависит, чего от счёта ждать: у брокерского есть ГО и
/// сверка по API, у банковского резерва — только ручные записи.
enum AccountKind {
  broker('Брокерский'),
  iis('ИИС'),
  crypto('Криптобиржа'),
  wallet('Кошелёк'),
  bank('Банковский резерв'),
  manual('Ручной'),
  paper('Бумажный');

  const AccountKind(this.label);
  final String label;

  /// Сверяется ли счёт с площадкой автоматически.
  bool get syncable =>
      this == AccountKind.broker || this == AccountKind.iis || this == AccountKind.crypto;

  static AccountKind parse(String raw) => AccountKind.values.firstWhere(
        (k) => k.name == raw,
        orElse: () => AccountKind.manual,
      );
}

/// Права ключа на площадке.
///
/// Право на вывод средств приложению не нужно ни при каком сценарии, и его
/// наличие — не «дополнительная возможность», а ошибка конфигурации: ключ с
/// выводом в руках приложения означает, что взлом устройства выносит деньги.
class ApiPermissions {
  const ApiPermissions({this.read = false, this.trade = false, this.withdraw = false});

  final bool read;
  final bool trade;
  final bool withdraw;

  String get label {
    final parts = [
      if (read) 'read',
      if (trade) 'trade',
      if (withdraw) 'withdraw',
    ];
    return parts.isEmpty ? 'нет прав' : parts.join(' + ');
  }

  /// Конфигурация в порядке: торговать можем, вывести — нет.
  bool get safe => !withdraw;

  Map<String, dynamic> toJson() => {'read': read, 'trade': trade, 'withdraw': withdraw};

  static ApiPermissions fromJson(Map<String, dynamic> json) => ApiPermissions(
        read: json['read'] as bool? ?? false,
        trade: json['trade'] as bool? ?? false,
        withdraw: json['withdraw'] as bool? ?? false,
      );
}

/// Счёт или кошелёк владельца.
class Account {
  const Account({
    required this.id,
    required this.title,
    required this.kind,
    required this.currency,
    this.venue,
    this.permissions = const ApiPermissions(),
    this.syncedAt,
    this.reconcile = ReconcileStatus.manual,
    this.note,
  });

  final String id;
  final String title;
  final AccountKind kind;

  /// Валюта учёта счёта.
  final Currency currency;

  /// Площадка: «Т-Инвестиции», «Bybit».
  final String? venue;

  final ApiPermissions permissions;

  /// Когда последний раз получали снимок площадки (UTC).
  final DateTime? syncedAt;

  final ReconcileStatus reconcile;
  final String? note;

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'kind': kind.name,
        'currency': currency.code,
        'scale': currency.scale,
        if (venue != null) 'venue': venue,
        'permissions': permissions.toJson(),
        if (syncedAt != null) 'synced': syncedAt!.toUtc().toIso8601String(),
        'reconcile': reconcile.name,
        if (note != null) 'note': note,
      };

  static Account fromJson(Map<String, dynamic> json) => Account(
        id: json['id'] as String,
        title: json['title'] as String,
        kind: AccountKind.parse(json['kind'] as String? ?? 'manual'),
        currency: Currency(
          json['currency'] as String? ?? 'RUB',
          (json['scale'] as num?)?.toInt() ?? 2,
        ),
        venue: json['venue'] as String?,
        permissions: ApiPermissions.fromJson(
            (json['permissions'] as Map<String, dynamic>?) ?? const {}),
        syncedAt: json['synced'] == null
            ? null
            : DateTime.parse(json['synced'] as String).toUtc(),
        reconcile: ReconcileStatus.parse(json['reconcile'] as String? ?? 'manual'),
        note: json['note'] as String?,
      );
}

/// Инвестиционный пакет — логическая корзина поверх книги, а не отдельный счёт.
///
/// Пакет отвечает на вопрос «зачем эти деньги здесь лежат»: горизонт, целевой
/// состав и правило, при котором замысел считается сломанным. Позиция
/// принадлежит пакету через атрибут события, поэтому сумма долей всегда равна
/// позиции — двойного учёта не возникает.
class CapitalPackage {
  const CapitalPackage({
    required this.id,
    required this.title,
    required this.contour,
    required this.thesis,
    required this.expectedReturnLow,
    required this.expectedReturnHigh,
    required this.drawdown95,
    required this.composition,
    this.isCurrent = false,
    this.rebuiltAt,
  });

  final String id;
  final String title;
  final Contour contour;

  /// Зачем пакет существует — одна-две строки.
  final String thesis;

  /// Ожидаемая доходность годовых, диапазон в процентах.
  final double expectedReturnLow;
  final double expectedReturnHigh;

  /// Просадка, которую пакет переживает в 95% сценариев за 12 месяцев, %.
  final double drawdown95;

  /// Целевой состав: класс актива → вес в процентах.
  final List<PackageSlice> composition;

  final bool isCurrent;
  final DateTime? rebuiltAt;

  Map<String, dynamic> toJson() => {
        'id': id,
        'title': title,
        'contour': contour.name,
        'thesis': thesis,
        'low': expectedReturnLow,
        'high': expectedReturnHigh,
        'dd': drawdown95,
        'current': isCurrent,
        if (rebuiltAt != null) 'rebuilt': rebuiltAt!.toUtc().toIso8601String(),
        'composition': [for (final s in composition) s.toJson()],
      };

  static CapitalPackage fromJson(Map<String, dynamic> json) => CapitalPackage(
        id: json['id'] as String,
        title: json['title'] as String,
        contour: Contour.parse(json['contour'] as String?) ?? Contour.core,
        thesis: json['thesis'] as String? ?? '',
        expectedReturnLow: (json['low'] as num?)?.toDouble() ?? 0,
        expectedReturnHigh: (json['high'] as num?)?.toDouble() ?? 0,
        drawdown95: (json['dd'] as num?)?.toDouble() ?? 0,
        isCurrent: json['current'] as bool? ?? false,
        rebuiltAt: json['rebuilt'] == null
            ? null
            : DateTime.parse(json['rebuilt'] as String).toUtc(),
        composition: [
          for (final s in (json['composition'] as List? ?? const []))
            PackageSlice.fromJson(s as Map<String, dynamic>),
        ],
      );
}

/// Доля пакета: класс актива, вес и примеры инструментов.
class PackageSlice {
  const PackageSlice({
    required this.assetClass,
    required this.weightPercent,
    this.examples = const [],
  });

  final String assetClass;
  final double weightPercent;
  final List<String> examples;

  Map<String, dynamic> toJson() =>
      {'class': assetClass, 'weight': weightPercent, 'examples': examples};

  static PackageSlice fromJson(Map<String, dynamic> json) => PackageSlice(
        assetClass: json['class'] as String? ?? '',
        weightPercent: (json['weight'] as num?)?.toDouble() ?? 0,
        examples: [for (final e in (json['examples'] as List? ?? const [])) e as String],
      );
}

/// Аллокация капитала по контурам: факт против цели.
class ContourAllocation {
  const ContourAllocation({
    required this.contour,
    required this.value,
    required this.targetPercent,
    required this.actualPercent,
  });

  final Contour contour;
  final Money value;
  final double targetPercent;
  final double actualPercent;

  /// Отклонение от цели в процентных пунктах.
  double get drift => actualPercent - targetPercent;
}
