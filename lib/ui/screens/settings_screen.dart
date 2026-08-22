import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/settings.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../../domain/broker/broker.dart';
import '../../domain/broker/tinvest_role.dart';
import '../../state/navigation.dart';
import '../layout.dart';
import '../../data/native_bridge.dart';
import '../widgets/engine_address_sheet.dart';
import '../widgets/broker_keys_sheet.dart';
import '../widgets/common.dart';
import '../widgets/risk_edit_sheet.dart';
import '../widgets/telegram_notifications_toggle.dart';
import '../widgets/vector_icon.dart';
import 'diagnostics_screen.dart';
import 'trading_diagnostics_screen.dart';

/// Экран «Настройки»: подключения, доставка сигналов, уведомления, риск (ТЗ §9).
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.snapshot, this.pill = 0});

  final SettingsSnapshot snapshot;

  /// Подраздел «Контроля»: риск и лимиты · интеграции · уведомления ·
  /// безопасность. Одна длинная лента настроек и была той картой, по которой
  /// невозможно быстро найти нужный рубильник.
  final int pill;

  /// Показывать ли карточки этого подраздела.
  bool _show(SettingsPill target) =>
      pill.clamp(0, SettingsPill.values.length - 1) == target.index;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
            children: [
              CardGrid(children: [
              // Режим: paper / shadow / live и допуск к живым деньгам.
              if (_show(SettingsPill.mode))
                if (controller.thinMode)
                  const _NothingHere(
                    title: 'Тонкий клиент · paper only',
                    note: 'Идеи, риск и сопровождение paper-сделок ведёт '
                        'сервер. Телефон показывает план и отправляет только '
                        'ваше явное решение; локального execution здесь нет.',
                  )
                else if (snapshot.trading != null) ...[
                    _TradingStatusCard(trading: snapshot.trading!),
                    _TradingControlsCard(trading: snapshot.trading!),
                  ] else
                  // Пустой экран без объяснения — худшее, что может показать
                  // раздел настроек: непонятно, сломалось или так задумано.
                  const _NothingHere(
                    title: 'Торговый контур не поднят',
                    note: 'Режим исполнения появляется, когда приложение '
                        'считает само и умеет ходить к брокеру. В режиме '
                        'данных макета исполнять нечего.',
                  ),

              // Риск: лимиты ТЗ §20 и правила размера позиции.
              if (_show(SettingsPill.risk))
                controller.thinMode
                    ? const _NothingHere(
                        title: 'Лимиты задаёт сервер',
                        note: 'Клиент не пересчитывает размер позиции и не '
                            'может обойти серверный risk gate. Действующие '
                            'лимиты показаны в методике движка.',
                      )
                    : _RiskCard(risk: snapshot.risk),

              if (_show(SettingsPill.data) && controller.thinMode)
                const _NothingHere(
                  title: 'Диагностика выполняется на сервере',
                  note: 'Thin-клиент не опрашивает MOEX и Bybit напрямую. '
                      'Свежесть и полнота серверных данных показаны в шапке '
                      'раздела; локальные market probes отключены.',
                ),

              // Интеграции: источники данных и площадки исполнения.
              if (_show(SettingsPill.connections)) ...[
                // Движок стоит первым: от него зависят идеи, то есть весь
                // смысл приложения. Пока его здесь не было, «все ключи
                // приняты, а лента пуста» не имело на экране объяснения.
                const _EngineCard(),
                if (!controller.thinMode &&
                    snapshot.exchanges.any((e) => e.isDataSource))
                  _ExchangesCard(
                    title: 'Источники данных',
                    exchanges: [
                      for (final e in snapshot.exchanges)
                        if (e.isDataSource) e,
                    ],
                    connectedLabel: 'Данные идут',
                    onConnect: controller.connectExchange,
                  )
                else if (!controller.thinMode)
                  _ExchangesCard(
                    title: 'Биржи · API',
                    exchanges: snapshot.exchanges,
                    connectedLabel: 'Подключено',
                    onConnect: controller.connectExchange,
                  ),
                if (!controller.thinMode && snapshot.trading != null)
                  _BrokersCard(trading: snapshot.trading!),
                // Токены — выше счетов: без токена счетов не будет вовсе.
                const TInvestTokensCard(),
                if (!controller.thinMode) ...[
                  const SizedBox(height: S.gap),
                  const _TinvestAccountsCard(),
                ],
                if (snapshot.background != null)
                  _BackgroundCard(background: snapshot.background!),
              ],

              // Уведомления: расписание и доставка.
              if (_show(SettingsPill.notifications) && controller.thinMode)
                const TelegramNotificationsToggle(),
              if (_show(SettingsPill.notifications) &&
                  !controller.thinMode &&
                  snapshot.channels.isEmpty &&
                  snapshot.notifications.isEmpty)
                const _NothingHere(
                  title: 'Каналы доставки не заданы',
                  note: 'Список появится, когда приложение узнает, куда '
                      'отправлять сигналы.',
                ),
              if (_show(SettingsPill.notifications) &&
                  (controller.thinMode
                      ? snapshot.notifications.isNotEmpty
                      : snapshot.channels.isNotEmpty ||
                          snapshot.notifications.isNotEmpty)) ...[
                if (!controller.thinMode)
                  _TogglesCard(
                    title: 'Доставка сигналов',
                    items: snapshot.channels,
                    onChanged: controller.toggleChannel,
                  ),
                _TogglesCard(
                  title: 'Уведомления',
                  items: snapshot.notifications,
                  onChanged: controller.toggleNotification,
                  // Доставку можно проверить, не дожидаясь настоящего сигнала:
                  // пуш с явно помеченным примером идеи.
                  footer: _TestPushButton(onTap: controller.sendTestPush),
                ),
              ],

              // Безопасность и прозрачность: чем проверяется доверие.
              if (_show(SettingsPill.security)) ...[
                // Доверие проверяется, а не декларируется: живой прогон
                // источников данных с вердиктами по каждому полю.
                if (!controller.thinMode)
                  SectionCard(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const SectionLabel('Прозрачность'),
                        const SizedBox(height: 6),
                        Text(
                          'Диагностика гоняет MOEX ISS и Bybit вживую и '
                          'сверяет разбор данных с реальностью биржи.',
                          style: T.body(11, color: C.muted, height: 1.5),
                        ),
                        const SizedBox(height: 10),
                        Pressable(
                          onTap: () => Navigator.of(context).push(
                            PageRouteBuilder<void>(
                              pageBuilder: (context, animation, secondary) =>
                                  const DiagnosticsScreen(),
                              transitionsBuilder:
                                  (context, animation, secondary, child) =>
                                      FadeTransition(
                                opacity: animation,
                                child: child,
                              ),
                            ),
                          ),
                          child: Container(
                            padding: const EdgeInsets.symmetric(vertical: 10),
                            decoration: BoxDecoration(
                              border: Border.all(color: C.borderHover),
                              borderRadius: BorderRadius.circular(R.inner),
                            ),
                            child: Center(
                              child: Text(
                                'Диагностика данных',
                                style:
                                    T.body(12, weight: 800, color: C.accent),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                const _AboutCard(),
              ],
              ]),
            ],
          ),
        ),
      ],
    );
  }
}

class _ExchangesCard extends StatelessWidget {
  const _ExchangesCard({
    required this.title,
    required this.exchanges,
    required this.connectedLabel,
    required this.onConnect,
  });

  final String title;
  final List<ExchangeAccount> exchanges;
  final String connectedLabel;
  final void Function(String id) onConnect;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 9, bottom: 3),
              child: SectionLabel(title),
            ),
            for (final exchange in exchanges)
              _ExchangeRow(
                exchange: exchange,
                connectedLabel: connectedLabel,
                onConnect: () => onConnect(exchange.id),
              ),
          ],
        ),
      );
}

class _ExchangeRow extends StatelessWidget {
  const _ExchangeRow({
    required this.exchange,
    required this.onConnect,
    this.connectedLabel = 'Подключено',
  });

  final ExchangeAccount exchange;
  final String connectedLabel;
  final VoidCallback onConnect;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.symmetric(vertical: 10),
        decoration: const BoxDecoration(
          border: Border(bottom: BorderSide(color: C.divider)),
        ),
        child: Row(
          children: [
            Container(
              width: 34,
              height: 34,
              alignment: Alignment.center,
              decoration: BoxDecoration(
                color: C.chip,
                borderRadius: BorderRadius.circular(R.inset),
              ),
              child: Text(
                exchange.abbr,
                style: T.jost(13, weight: 700, color: Color(exchange.accentHex)),
              ),
            ),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(exchange.name, style: T.body(13, weight: 700)),
                  const SizedBox(height: 1),
                  Text(exchange.subtitle, style: T.body(11, color: C.muted)),
                ],
              ),
            ),
            const SizedBox(width: 10),
            Pressable(
              onTap: exchange.connected ? null : onConnect,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  border: Border.all(
                    color: exchange.connected ? C.greenBorder : const Color(0x59FFD400),
                  ),
                  borderRadius: BorderRadius.circular(R.chipLg),
                ),
                child: Text(
                  exchange.connected ? connectedLabel : 'Подключить',
                  style: T.body(
                    11,
                    weight: 700,
                    color: exchange.connected ? C.green : C.accent,
                  ),
                ),
              ),
            ),
          ],
        ),
      );
}

/// Торговый контур: режим, ключи, допуск и аварийная остановка.
///
/// Всё, от чего зависит, уйдёт ли ордер, собрано в одном месте и написано
/// прямо. Приложение, которому доверяют счёт, не имеет права прятать это
/// в подменю.
/// Можно ли сейчас торговать — и если нет, почему.
class _TradingStatusCard extends StatelessWidget {
  const _TradingStatusCard({required this.trading});

  final TradingView trading;

  @override
  Widget build(BuildContext context) {
    final color = trading.killSwitch
        ? C.red
        : trading.ready
            ? C.green
            : C.muted;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Торговый контур')),
              OutlineBadge(
                label: trading.killSwitch
                    ? 'ОСТАНОВЛЕН'
                    : trading.ready
                        ? 'ГОТОВ'
                        : 'НЕ ГОТОВ',
                color: color,
                borderColor: color,
                fontWeight: 700,
              ),
            ],
          ),
          const SizedBox(height: 8),

          // Чего не хватает до готовности — одной строкой и сразу под
          // бейджем: «НЕ ГОТОВ» без причины заставляет гадать.
          if (!trading.ready && trading.blockingReason.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                'Ордер сейчас не уйдёт: ${trading.blockingReason}.',
                style: T.body(11.5, color: C.red, height: 1.4),
              ),
            ),

          if (!trading.vaultAvailable)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                'Защищённое хранилище на этом устройстве недоступно — ключи '
                'биржи сохранять некуда. Торговля отключена.',
                style: T.body(11.5, color: C.red, height: 1.4),
              ),
            ),

          KeyValueRow(
            name: 'Подтверждение сделки',
            value: trading.confirmMethod.label,
            valueStyle: T.mono(
              12,
              color: trading.confirmMethod.available ? C.text : C.red,
            ),
          ),
          if (!trading.confirmMethod.available)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 6),
              child: Text(
                trading.confirmMethod.hint,
                style: T.body(11, color: C.red, height: 1.4),
              ),
            ),
          KeyValueRow(
            name: 'Допуск к живым деньгам',
            value: trading.gateAllowed ? 'открыт' : 'закрыт',
            showDivider: false,
            valueStyle: T.mono(12, color: trading.gateAllowed ? C.green : C.muted),
          ),
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: Text(trading.gateReason, style: T.body(11, color: C.muted, height: 1.4)),
          ),
        ],
      ),
    );
  }
}

/// Площадки: режим и ключи по каждой отдельно.
class _BrokersCard extends StatelessWidget {
  const _BrokersCard({required this.trading});

  final TradingView trading;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Площадки'),
          const SizedBox(height: 4),

          // Каждая площадка со своим режимом и ключами: крипта может стоять
          // на testnet, пока российский счёт ещё в песочнице.
          for (final broker in trading.brokers) ...[
            Padding(
              padding: const EdgeInsets.only(top: 4, bottom: 2),
              child: Text(broker.title, style: T.body(12.5, weight: 700)),
            ),
            KeyValueRow(
              name: 'Режим',
              value: '${broker.modeLabel} · сменить',
              valueStyle: T.mono(12, color: broker.live ? C.red : C.text),
              onTap: () => controller.setTradingMode(
                BrokerId.parse(broker.id),
                broker.live ? TradingMode.testnet : TradingMode.live,
              ),
            ),
            if (broker.modeNote.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2, bottom: 4),
                child: Text(
                  broker.modeNote,
                  style: T.body(10.5, color: C.muted, height: 1.4),
                ),
              ),
            // У Т-Инвестиций ключей три, и живут они в своей карточке ниже.
            // Строка «Ключи» здесь спрашивала только один из трёх и отвечала
            // «не заданы» при трёх принятых токенах: два места про одно и то
            // же с разными ответами — это не подсказка, а путаница.
            if (BrokerId.parse(broker.id) == BrokerId.tinvest)
              Padding(
                padding: const EdgeInsets.only(top: 2, bottom: 4),
                child: Text(
                  'Токены — в карточке «Токены Т-Инвестиций» ниже: их три, с '
                  'разными правами. Режим выше выбирает, какой из них '
                  'торгует: тренировка — песочница, live — торговый счёт.',
                  style: T.body(10.5, color: C.muted, height: 1.4),
                ),
              )
            else
            KeyValueRow(
              name: 'Ключи',
              // Три разных состояния, а не два: ключ может лежать в хранилище
              // и при этом быть отвергнутым биржей — молчать об этом нельзя.
              value: !broker.hasKeys
                  ? 'не заданы · ввести'
                  : broker.keysAccepted
                      ? 'приняты · изменить'
                      : 'НЕ ПРИНЯТЫ · изменить',
              valueStyle: T.mono(
                12,
                color: !broker.hasKeys
                    ? C.accent
                    : broker.keysAccepted
                        ? C.green
                        : C.red,
              ),
              onTap: () => showBrokerKeysSheet(
                context,
                broker: BrokerId.parse(broker.id),
                mode: broker.live ? TradingMode.live : TradingMode.testnet,
                onSubmit: (key, secret) =>
                    controller.saveBrokerKeys(BrokerId.parse(broker.id), key, secret),
              ),
            ),
            if (broker.keyNote.isNotEmpty &&
                BrokerId.parse(broker.id) != BrokerId.tinvest)
              Padding(
                padding: const EdgeInsets.only(top: 2, bottom: 4),
                child: Text(
                  broker.keyNote,
                  style: T.body(
                    10.5,
                    color: broker.keysAccepted ? C.muted : C.red,
                    height: 1.4,
                  ),
                ),
              ),
            // Ключ привязан к паре «площадка + режим». Если ключи есть, но не
            // для выбранного режима, площадка выглядит как «ключей нет» — и
            // молча пропадает из капитала. Пишем это прямым текстом.
            if (BrokerId.parse(broker.id) != BrokerId.tinvest)
              ?_modeMismatchNote(controller, BrokerId.parse(broker.id)),
            // Общая причина уже написана выше в блоке «Допуск» — здесь только
            // то, что относится именно к этой площадке.
            if (!broker.liveAllowed && broker.liveBlockedReason != trading.gateReason)
              Padding(
                padding: const EdgeInsets.only(top: 2, bottom: 6),
                child: Text(
                  'Живой режим закрыт: ${broker.liveBlockedReason}.',
                  style: T.body(10.5, color: C.muted, height: 1.4),
                ),
              ),
            const SizedBox(height: 4),
          ],
        ],
      ),
    );
  }

  /// Строка о расхождении режима и ключей. null — расхождения нет.
  static Widget? _modeMismatchNote(AppController controller, BrokerId id) {
    final venue = controller.venues.where((v) => v.id == id).firstOrNull;
    if (venue == null || !venue.hasKeys || venue.modeMatches) return null;
    return Padding(
      padding: const EdgeInsets.only(top: 2, bottom: 4),
      child: Text(
        'Ключи заведены для режима ${venue.keyModes.map((m) => m.name).join(' и ')}, '
        'а переключатель стоит на ${venue.mode.name}. Капитал и позиции '
        'читаются ключом, который есть; заявки с этого режима не уйдут.',
        style: T.body(10.5, color: C.warning, height: 1.4),
      ),
    );
  }
}

/// Рубильники контура и проверка его словами биржи.
class _TradingControlsCard extends StatelessWidget {
  const _TradingControlsCard({required this.trading});

  final TradingView trading;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Управление'),
          const SizedBox(height: 10),
          _TradingSwitch(
            title: 'Отправлять ордера',
            subtitle: 'Каждая сделка подтверждается отдельно — молча ничего '
                'не отправляется',
            value: trading.enabled,
            onChanged: controller.setTradingEnabled,
          ),
          const SizedBox(height: 10),
          _TradingSwitch(
            title: 'Аварийная остановка',
            subtitle: 'Снимает активные заявки и запрещает новые. Снимается '
                'только руками',
            value: trading.killSwitch,
            danger: true,
            onChanged: controller.setKillSwitch,
          ),
          const SizedBox(height: 12),
          // Состояние выше — словами приложения. Здесь его можно проверить
          // словами биржи: что она отвечает на наш ключ и что видит на счёте.
          Pressable(
            onTap: () {
              final desk = controller.tradingDesk;
              if (desk == null) return;
              Navigator.of(context).push(
                PageRouteBuilder<void>(
                  pageBuilder: (context, animation, secondary) =>
                      TradingDiagnosticsScreen(desk: desk),
                  transitionsBuilder: (context, animation, secondary, child) =>
                      FadeTransition(opacity: animation, child: child),
                ),
              );
            },
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 10),
              decoration: BoxDecoration(
                border: Border.all(color: C.borderHover),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: Center(
                child: Text(
                  'Диагностика торговли',
                  style: T.body(12, weight: 800, color: C.accent),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Фоновая работа: что происходит, пока приложение закрыто.
///
/// Ограничения платформы написаны прямо в карточке. Обещать «следит всегда»
/// и молча замолкать через шесть часов — ровно тот обман, из-за которого
/// приложению перестают доверять.
class _BackgroundCard extends StatelessWidget {
  const _BackgroundCard({required this.background});

  final BackgroundView background;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Фоновая работа'),
          const SizedBox(height: 6),
          _TradingSwitch(
            title: 'Следить, пока приложение закрыто',
            subtitle: 'Короткий опрос сервера примерно раз в 15 минут: новые '
                'идеи «Можно действовать» и важные переходы paper-сделок',
            value: background.enabled,
            onChanged: controller.setBackgroundEnabled,
          ),
          const SizedBox(height: 10),
          KeyValueRow(
            name: 'Состояние',
            value: background.stateNote,
            showDivider: false,
            valueStyle: T.mono(
              12,
              color: background.stateNote.contains('сбой') ? C.red : C.muted,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Сервер ведёт принятую paper-сделку независимо от телефона. '
            'Обнаружение новой идеи и уведомления через Android polling — '
            'best-effort: Doze может отложить запуск дольше 15 минут.',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
          const SizedBox(height: 6),
          Text(
            'После принудительной остановки приложения Android не запускает '
            'опрос до следующего открытия. Разрешите уведомления и при '
            'необходимости исключите SignalAI из глубокого сна батареи.',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
        ],
      ),
    );
  }
}

class _TradingSwitch extends StatelessWidget {
  const _TradingSwitch({
    required this.title,
    required this.subtitle,
    required this.value,
    required this.onChanged,
    this.danger = false,
  });

  final String title;
  final String subtitle;
  final bool value;
  final bool danger;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) => Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: T.body(13, weight: 600, color: danger && value ? C.red : C.text),
                ),
                const SizedBox(height: 2),
                Text(subtitle, style: T.body(11, color: C.muted, height: 1.4)),
              ],
            ),
          ),
          const SizedBox(width: 10),
          AppToggle(value: value, onChanged: onChanged),
        ],
      );
}

class _TogglesCard extends StatelessWidget {
  const _TogglesCard({
    required this.title,
    required this.items,
    required this.onChanged,
    this.footer,
  });

  final String title;
  final List<ToggleSetting> items;
  final void Function(String id, bool enabled) onChanged;

  /// Дополнительный элемент под тумблерами — например, кнопка проверки.
  final Widget? footer;

  @override
  Widget build(BuildContext context) => SectionCard(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 9, bottom: 3),
              child: SectionLabel(title),
            ),
            for (final item in items)
              Container(
                padding: const EdgeInsets.symmetric(vertical: 10),
                decoration: const BoxDecoration(
                  border: Border(bottom: BorderSide(color: C.divider)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(item.name, style: T.body(13, weight: 700)),
                          const SizedBox(height: 1),
                          Text(item.subtitle, style: T.body(11, color: C.muted)),
                        ],
                      ),
                    ),
                    const SizedBox(width: 11),
                    AppToggle(
                      value: item.enabled,
                      onChanged: (value) => onChanged(item.id, value),
                    ),
                  ],
                ),
              ),
            if (footer != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 10),
                child: footer,
              ),
          ],
        ),
      );
}

/// Кнопка тестового пуша: пример идеи в шторке уведомлений.
class _TestPushButton extends StatelessWidget {
  const _TestPushButton({required this.onTap});

  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => Pressable(
        onTap: onTap,
        child: Container(
          width: double.infinity,
          padding: const EdgeInsets.symmetric(vertical: 10),
          decoration: BoxDecoration(
            border: Border.all(color: C.borderHover),
            borderRadius: BorderRadius.circular(R.inner),
          ),
          child: Center(
            child: Text(
              'Отправить тестовый пуш',
              style: T.body(12, weight: 800, color: C.accent),
            ),
          ),
        ),
      );
}

/// Риск-профиль. Депозит и процент риска редактируются — объёмы позиций
/// пересчитываются во всём приложении (ТЗ §6, §9).
class _RiskCard extends StatelessWidget {
  const _RiskCard({required this.risk});

  final RiskProfile risk;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Риск-профиль'),
          const SizedBox(height: 4),
          KeyValueRow(
            name: 'Депозит',
            value: '${fmt(risk.deposit, 0)} ₽',
            valueStyle: T.mono(12, weight: 600),
            onTap: () => showRiskEditSheet(
              context,
              risk: risk,
              field: RiskField.deposit,
              onSubmit: (value) => controller.updateRisk(deposit: value),
            ),
          ),
          KeyValueRow(
            name: 'Риск на сделку',
            value: '${riskPercentLabel(risk.riskPercent)} · ${fmt(risk.riskRub, 0)} ₽',
            valueStyle: T.mono(12, weight: 600),
            onTap: () => showRiskEditSheet(
              context,
              risk: risk,
              field: RiskField.riskPercent,
              onSubmit: (value) => controller.updateRisk(riskPercent: value),
            ),
          ),
          KeyValueRow(
            name: 'Дневной лимит потерь',
            value: risk.dailyLossLimit,
            valueStyle: T.mono(12, weight: 600),
          ),
          KeyValueRow(
            name: 'Одновременных сделок',
            value: risk.maxConcurrentTrades,
            valueStyle: T.mono(12, weight: 600),
          ),
          KeyValueRow(
            name: 'После 2 SL подряд',
            value: risk.pauseRule,
            valueStyle: T.mono(12, weight: 600),
          ),
          const SizedBox(height: 8),
          Text(
            'Все сделки — только после подтверждения. После подтверждения ордер и OCO '
            'выставляются автоматически.',
            style: T.body(10.5, color: C.muted, height: 1.5),
          ),
        ],
      ),
    );
  }
}

/// «О приложении»: знак, имя и версия установленной сборки.
///
/// Версия спрашивается у системы, а не пишется в коде: при разборе «что за
/// сборка стоит на устройстве» строка из интерфейса должна совпадать со
/// строкой из настроек Android, иначе она только запутывает.
class _AboutCard extends StatefulWidget {
  const _AboutCard();

  @override
  State<_AboutCard> createState() => _AboutCardState();
}

class _AboutCardState extends State<_AboutCard> {
  String? _version;

  @override
  void initState() {
    super.initState();
    const NativeBridge().appVersion().then((value) {
      if (mounted) setState(() => _version = value);
    });
  }

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Row(
          children: [
            const BrandMark(size: 52),
            const SizedBox(width: 13),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text.rich(
                    TextSpan(
                      text: 'Signal',
                      style: T.jost(17),
                      children: [
                        TextSpan(text: 'AI', style: T.jost(17, color: C.accent)),
                      ],
                    ),
                  ),
                  const SizedBox(height: 3),
                  Text(
                    _version == null
                        ? 'личная сборка · версию спрашиваем у системы'
                        : 'версия $_version · личная сборка',
                    style: T.mono(11, color: C.muted),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
}


/// Три токена Т-Инвестиций.
///
/// Токен Invest API привязан к пользователю, а не к счёту: один торговый
/// токен видит все счета владельца и может отправить заявку с любого.
/// Ограничить его на стороне брокера нельзя — значит нельзя и заводить один
/// такой токен. Права разделены там, где их выдаёт брокер, и заводятся тремя
/// отдельными строками, чтобы это было видно, а не подразумевалось.
class TInvestTokensCard extends StatefulWidget {
  const TInvestTokensCard({super.key});

  @override
  State<TInvestTokensCard> createState() => _TInvestTokensCardState();
}

class _TInvestTokensCardState extends State<TInvestTokensCard> {
  @override
  void initState() {
    super.initState();
    // Наличие токенов читается из хранилища, а не угадывается по режиму.
    WidgetsBinding.instance.addPostFrameCallback(
      (_) => AppScope.read(context).refreshTinvestTokens(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final present = controller.tinvestTokens;
    final roles = controller.thinMode
        ? const [TInvestRole.invest]
        : TInvestRole.values;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionLabel(
            controller.thinMode
                ? 'Т-Инвестиции · метаданные'
                : 'Токены Т-Инвестиций',
          ),
          const SizedBox(height: 6),
          Text(
            controller.thinMode
                ? 'Только read-only токен: приложение обновляет снимок '
                    'инструментов и секторов. Он остаётся на устройстве; '
                    'торговые токены и отправка заявок в thin отключены.'
                : 'Токен Invest API привязан к вам, а не к счёту: один '
                    'торговый токен видит все ваши счета и может отправить '
                    'заявку с любого. Поэтому их три, с разными правами.',
            style: T.body(11.5, color: C.muted, height: 1.5),
          ),
          const SizedBox(height: 10),
          for (final role in roles) ...[
            KeyValueRow(
              name: role.title,
              value: present.contains(role) ? 'задан · изменить' : 'нет · ввести',
              valueStyle: T.mono(
                12,
                color: present.contains(role) ? C.green : C.accent,
              ),
              onTap: () => showBrokerKeysSheet(
                context,
                broker: BrokerId.tinvest,
                mode: role == TInvestRole.trade
                    ? TradingMode.live
                    : TradingMode.testnet,
                onSubmit: (token, _) =>
                    controller.saveTinvestToken(role, token),
              ),
            ),
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 6),
              child: Text(
                role.meaning,
                style: T.body(10.5, color: C.faint, height: 1.4),
              ),
            ),
          ],
        ],
      ),
    );
  }
}


/// Счета Т-Инвестиций и выбор торгового.
///
/// Токен Invest API привязан к пользователю, а не к счёту: он видит и
/// фьючерсный счёт, и тот, где лежит основной капитал. Все они читаются для
/// книги, но торговать приложение имеет право только с одного — выбранного
/// здесь. Заявка, ушедшая не с того счёта, ломает и учёт, и налоги.
/// Движок SignalAI — сервер, который считает идеи (§18).
///
/// Раньше его адрес существовал только как параметр сборки и в приложении не
/// показывался нигде. Получалось непроходимое место: ключи бирж приняты,
/// данные идут, а лента идей пуста — и объяснения этому на экране нет.
/// Главная зависимость приложения обязана быть видимой и исправимой без
/// пересборки.
class _EngineCard extends StatelessWidget {
  const _EngineCard();

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final url = controller.engineBaseUrl;
    final set = url.isNotEmpty;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Движок SignalAI')),
              OutlineBadge(
                label: set ? 'адрес задан' : 'адреса нет',
                color: set ? C.green : C.red,
                borderColor: (set ? C.green : C.red).withValues(alpha: 0.35),
                background: (set ? C.green : C.red).withValues(alpha: 0.12),
                fontWeight: 700,
                radius: R.pill,
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            set
                ? 'Идеи, оценка §15.1 и разметка приходят отсюда. Ключи бирж '
                    'к этому адресу отношения не имеют: они дают котировки и '
                    'счёт, а идеи считает сервер.'
                : 'Идеи считает сервер, и без его адреса лента пуста — сколько '
                    'бы бирж ни было подключено. Это не «сегодня нет сетапов».',
            style: T.body(11.5, color: set ? C.muted : C.warning, height: 1.5),
          ),
          const SizedBox(height: 10),
          InsetBox(
            padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Адрес', color: C.faint),
                const SizedBox(height: 4),
                Text(
                  set ? url : 'не задан',
                  style: T.mono(12, weight: 600, color: set ? C.text : C.faint),
                ),
                const SizedBox(height: 3),
                Text(
                  [
                    controller.engineFromSettings
                        ? 'задан здесь, в приложении'
                        : (set ? 'зашит при сборке' : 'ни в сборке, ни здесь'),
                    controller.engineTokenSet ? 'токен задан' : 'токена нет',
                  ].join(' · '),
                  style: T.body(10.5, color: C.dim),
                ),
              ],
            ),
          ),
          if (controller.engineProbe != null) ...[
            const SizedBox(height: 8),
            Text(
              controller.engineProbe!,
              style: T.body(11, color: C.textSecondary, height: 1.45),
            ),
          ],
          if (controller.engineAuthIssue != null) ...[
            const SizedBox(height: 8),
            Text(
              controller.engineAuthIssue!,
              style: T.body(11, color: C.warning, height: 1.45),
            ),
          ],
          if (controller.engineProbing) ...[
            const SizedBox(height: 8),
            const BusyLine(label: 'Спрашиваем движок…'),
          ],
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: ActionButton(
                  label: set ? 'Изменить адрес' : 'Задать адрес',
                  dense: true,
                  onTap: () => showEngineAddressSheet(
                    context,
                    current: controller.engineBaseUrl,
                    currentToken: '',
                    onSubmit: (url, token, pairingSessionId) => controller.setEngineBaseUrl(
                      url,
                      token: token,
                      pairingSessionId: pairingSessionId,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: ActionButton(
                  label: 'Проверить связь',
                  dense: true,
                  onTap: controller.engineProbing ? null : controller.probeEngine,
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _TinvestAccountsCard extends StatefulWidget {
  const _TinvestAccountsCard();

  @override
  State<_TinvestAccountsCard> createState() => _TinvestAccountsCardState();
}

class _TinvestAccountsCardState extends State<_TinvestAccountsCard> {
  bool _asked = false;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final accounts = controller.tinvestAccounts;
    final selected = controller.tinvestTradingAccount;
    final allowed = controller.tinvestAllowedAccounts;

    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const SectionLabel('Счета Т-Инвестиций'),
          const SizedBox(height: 6),
          if (accounts.isEmpty)
            Text(
              _asked
                  ? 'Счета не пришли: токен не задан или брокер не ответил.'
                  : 'Токен видит все ваши счета. Приложение работает только '
                      'с теми, которым вы открыли доступ: остальные не '
                      'попадают ни в капитал, ни в выбор торгового. Заявки '
                      'уходят с одного — отмеченного «ТОРГОВЫЙ».',
              style: T.body(11.5, color: C.muted, height: 1.5),
            )
          else
            for (final account in accounts) ...[
              Pressable(
                // Нажатие назначает торговый счёт. Разрешение читать —
                // отдельная кнопка справа: это разные права, и объединять
                // их одним жестом значило бы выдавать оба сразу.
                onTap: account.tradable && allowed.contains(account.id)
                    ? () => controller.setTinvestAccount(account.id)
                    : null,
                child: Container(
                  padding: const EdgeInsets.symmetric(vertical: 9),
                  decoration: const BoxDecoration(
                    border: Border(bottom: BorderSide(color: C.divider)),
                  ),
                  child: Row(
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          color: account.id == selected
                              ? C.accent
                              : account.tradable
                                  ? C.borderHover
                                  : C.info,
                          shape: BoxShape.circle,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(account.title,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: T.body(12.5, weight: 700)),
                            Text(
                              account.tradable
                                  ? 'полный доступ'
                                  : 'только чтение — капитал считаем, не торгуем',
                              style: T.body(10.5, color: C.muted),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        account.id == selected ? 'ТОРГОВЫЙ' : '',
                        style: T.body(9.5, weight: 800, color: C.accent),
                      ),
                      const SizedBox(width: 8),
                      // Разрешение читать счёт. Пока его нет, счёт не
                      // попадает ни в капитал, ни в выбор торгового.
                      Pressable(
                        onTap: () => controller.setTinvestAccountAccess(
                          account.id,
                          !allowed.contains(account.id),
                        ),
                        child: OutlineBadge(
                          label: allowed.contains(account.id)
                              ? 'доступ есть'
                              : 'закрыт',
                          color: allowed.contains(account.id) ? C.green : C.muted,
                          borderColor: (allowed.contains(account.id)
                                  ? C.green
                                  : C.muted)
                              .withValues(alpha: 0.35),
                          background: (allowed.contains(account.id)
                                  ? C.green
                                  : C.muted)
                              .withValues(alpha: 0.12),
                          fontWeight: 700,
                          padding: const EdgeInsets.symmetric(
                              horizontal: 7, vertical: 3),
                          radius: R.pill,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          const SizedBox(height: 10),
          Pressable(
            onTap: () {
              setState(() => _asked = true);
              controller.refreshTinvestAccounts();
            },
            child: Container(
              padding: const EdgeInsets.symmetric(vertical: 10),
              decoration: BoxDecoration(
                border: Border.all(color: C.borderHover),
                borderRadius: BorderRadius.circular(R.inner),
              ),
              child: Center(
                child: Text(
                  accounts.isEmpty ? 'Показать счета' : 'Обновить список',
                  style: T.body(12, weight: 800, color: C.accent),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Честная заглушка подраздела.
///
/// Пустой экран без единого слова читается как поломка: владелец не может
/// отличить «здесь пока нечего показывать» от «приложение сломалось». Раздел
/// настроек обязан отвечать на этот вопрос сам.
class _NothingHere extends StatelessWidget {
  const _NothingHere({required this.title, required this.note});

  final String title;
  final String note;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: T.body(13, weight: 800)),
            const SizedBox(height: 6),
            Text(note, style: T.body(11.5, color: C.muted, height: 1.5)),
          ],
        ),
      );
}
