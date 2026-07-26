import 'broker.dart';

/// Один проверенный факт торгового контура.
///
/// Тот же формат, что у диагностики данных: сырые значения плюс вердикт.
/// Верить на слово тут нечему — либо ключ принят биржей, либо нет.
class TradingCheck {
  const TradingCheck({
    required this.name,
    required this.ok,
    required this.details,
  });

  final String name;
  final bool ok;

  /// Строки как есть: ответ биржи, текст отказа, счётчики. Без пересказа.
  final List<String> details;
}

/// Всё, что диагностика спрашивает у приложения.
///
/// Узкий интерфейс, а не прямая зависимость от репозитория: проверка
/// работает в тестах на подставном контуре, без Keystore и сети.
abstract interface class TradingProbe {
  /// Доступно ли защищённое хранилище ключей.
  Future<bool> get vaultAvailable;

  /// Чем подтверждается сделка на этом устройстве.
  Future<ConfirmMethod> get confirmMethod;

  /// Разрешена ли отправка ордеров и не нажата ли аварийная остановка.
  bool get tradingEnabled;
  bool get killSwitch;

  /// Площадки, которые нужно проверить.
  List<BrokerId> get probedBrokers;

  /// Название текущего режима площадки словами самой площадки.
  String modeLabelOf(BrokerId broker);

  /// Есть ли ключи площадки для её текущего режима.
  Future<bool> hasBrokerKeys(BrokerId broker);

  /// Итог последней проверки ключей: почему их приняли или отвергли.
  String? keyNoteOf(BrokerId broker);

  /// Брокер площадки в её текущем режиме.
  Broker brokerOf(BrokerId broker);
}

/// Живой прогон торгового контура: от хранилища ключей до счёта на бирже.
///
/// Проверки идут потоком, чтобы экран показывал их по мере готовности:
/// обращение к бирже занимает секунды, и ждать все ответы, глядя в пустоту,
/// незачем.
Stream<TradingCheck> diagnoseTradingWith(TradingProbe probe) async* {
  yield await _guard('Защищённое хранилище ключей', () async {
    final available = await probe.vaultAvailable;
    return TradingCheck(
      name: 'Защищённое хранилище ключей',
      ok: available,
      details: available
          ? const [
              'Android Keystore доступен',
              'секрет биржи не покидает нативную часть: подпись считается там же',
            ]
          : const [
              'Keystore недоступен — ключи биржи сохранять некуда',
              'торговля на этом устройстве работать не будет',
            ],
    );
  });

  yield await _guard('Подтверждение сделки', () async {
    final method = await probe.confirmMethod;
    return TradingCheck(
      name: 'Подтверждение сделки',
      ok: method.available,
      details: [
        'способ: ${method.label}',
        if (method.available)
          'каждая заявка подтверждается отдельно — молча ничего не уходит'
        else
          method.hint,
      ],
    );
  });

  yield TradingCheck(
    name: 'Отправка ордеров',
    ok: probe.tradingEnabled && !probe.killSwitch,
    details: [
      'тумблер «Отправлять ордера»: ${probe.tradingEnabled ? 'включён' : 'выключен'}',
      if (probe.killSwitch)
        'активна аварийная остановка: новые заявки запрещены'
      else
        'аварийная остановка не активна',
      if (!probe.tradingEnabled)
        'идеи считаются и ведутся на бумаге, но на биржу ничего не уходит',
    ],
  );

  for (final id in probe.probedBrokers) {
    final mode = probe.modeLabelOf(id);
    final hasKeys = await probe.hasBrokerKeys(id);
    final note = probe.keyNoteOf(id);

    yield TradingCheck(
      name: '${id.title}: ключи',
      ok: hasKeys,
      details: [
        'режим: $mode',
        hasKeys
            ? 'ключ и секрет лежат в Keystore'
            : 'ключи для этого режима не заданы — введите их в настройках',
        if (note != null) 'последняя проверка: $note',
      ],
    );

    if (!hasKeys) {
      yield TradingCheck(
        name: '${id.title}: связь со счётом',
        ok: false,
        details: const ['проверять нечем: ключи не заданы'],
      );
      continue;
    }

    var reachable = false;
    yield await _guard('${id.title}: связь со счётом', () async {
      try {
        final answer = await probe.brokerOf(id).checkAccess();
        reachable = true;
        return TradingCheck(
          name: '${id.title}: связь со счётом',
          ok: true,
          details: [answer, 'ключ принят: счёт отвечает'],
        );
      } on BrokerException catch (e) {
        return TradingCheck(
          name: '${id.title}: связь со счётом',
          ok: false,
          // Полный текст отказа, а не пересказ: в нём код биржи, по которому
          // и понятно, что чинить — права ключа, IP-фильтр или сам ключ.
          details: [e.message],
        );
      }
    });
    if (!reachable) continue;

    yield await _guard('${id.title}: позиции и заявки', () async {
      final broker = probe.brokerOf(id);
      final positions = await broker.positions();
      final orders = await broker.orders();
      final naked = await broker.unprotectedPositions();
      return TradingCheck(
        name: '${id.title}: позиции и заявки',
        ok: naked.isEmpty,
        details: [
          'открытых позиций: ${positions.length}',
          for (final p in positions)
            '· ${p.symbol} ${p.long ? 'long' : 'short'} ${_amount(p.quantity)} '
                'по ${_amount(p.entryPrice)} · ${_stopNote(p)} · '
                'результат ${_signed(p.unrealizedPnl)}',
          'активных заявок: ${orders.length}',
          for (final o in orders)
            '· ${o.symbol} ${_amount(o.quantity)} по ${_amount(o.price)} — ${o.status}',
          if (naked.isEmpty)
            'позиций без защитного стопа нет'
          else
            'БЕЗ СТОПА: ${naked.map((p) => p.symbol).join(', ')} — '
                'фоновый контур попробует восстановить защиту',
        ],
      );
    });
  }
}

/// Состояние защиты позиции словами, а не числом.
///
/// Площадки отдают разное: Bybit — цену стопа вместе с позицией, брокер —
/// только факт, что стоп-заявка есть. Печатать в обоих случаях число значило
/// бы выдавать признак за уровень.
String _stopNote(BrokerPosition position) {
  if (position.unprotected) return 'БЕЗ СТОПА';
  return position.stopLoss > 0
      ? 'стоп ${_amount(position.stopLoss)}'
      : 'стоп-заявка на бирже есть';
}

/// Число без хвоста `.0` и с запятой — как в остальном приложении.
String _amount(double value) {
  final text = value == value.roundToDouble() && value.abs() < 1e15
      ? value.toStringAsFixed(0)
      : value.toStringAsFixed(value.abs() < 100 ? 4 : 2);
  return text.replaceAll('.', ',');
}

String _signed(double value) => '${value > 0 ? '+' : ''}${_amount(value)}';

/// Любой отказ — это результат проверки, а не падение экрана.
Future<TradingCheck> _guard(String name, Future<TradingCheck> Function() body) async {
  try {
    return await body();
  } on Object catch (e) {
    return TradingCheck(name: name, ok: false, details: ['ошибка: $e']);
  }
}
