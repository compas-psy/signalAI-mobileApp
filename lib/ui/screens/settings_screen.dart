import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/settings.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../../domain/broker/broker.dart';
import '../../monitor/background_mode.dart';
import '../widgets/broker_keys_sheet.dart';
import '../widgets/common.dart';
import '../widgets/risk_edit_sheet.dart';
import 'diagnostics_screen.dart';

/// Экран «Настройки»: подключения, доставка сигналов, уведомления, риск (ТЗ §9).
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key, required this.snapshot});

  final SettingsSnapshot snapshot;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const ScreenHeader(title: 'Настройки'),
        Expanded(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 90),
            children: [
              // Источники данных и торговый доступ — принципиально разные
              // вещи, и смешивать их в одну секцию «Биржи» нечестно: активный
              // поток котировок не означает, что можно торговать.
              if (snapshot.exchanges.any((e) => e.isDataSource)) ...[
                _ExchangesCard(
                  title: 'Источники данных',
                  exchanges: [
                    for (final e in snapshot.exchanges)
                      if (e.isDataSource) e,
                  ],
                  connectedLabel: 'Данные идут',
                  onConnect: controller.connectExchange,
                ),
                const SizedBox(height: 12),
                _ExchangesCard(
                  title: 'Торговый доступ',
                  exchanges: [
                    for (final e in snapshot.exchanges)
                      if (!e.isDataSource) e,
                  ],
                  connectedLabel: 'Подключено',
                  onConnect: controller.connectExchange,
                ),
              ] else
                _ExchangesCard(
                  title: 'Биржи · API',
                  exchanges: snapshot.exchanges,
                  connectedLabel: 'Подключено',
                  onConnect: controller.connectExchange,
                ),
              if (snapshot.trading != null) ...[
                const SizedBox(height: 12),
                _TradingCard(trading: snapshot.trading!),
              ],
              if (snapshot.background != null) ...[
                const SizedBox(height: 12),
                _BackgroundCard(background: snapshot.background!),
              ],
              const SizedBox(height: 12),
              _TogglesCard(
                title: 'Доставка сигналов',
                items: snapshot.channels,
                onChanged: controller.toggleChannel,
              ),
              const SizedBox(height: 12),
              _TogglesCard(
                title: 'Уведомления',
                items: snapshot.notifications,
                onChanged: controller.toggleNotification,
              ),
              const SizedBox(height: 12),
              _RiskCard(risk: snapshot.risk),
              const SizedBox(height: 12),
              // Доверие проверяется, а не декларируется: живой прогон
              // источников данных с вердиктами по каждому полю.
              SectionCard(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const SectionLabel('Прозрачность'),
                    const SizedBox(height: 6),
                    Text(
                      'Диагностика гоняет MOEX ISS и Bybit вживую и сверяет '
                      'разбор данных с реальностью биржи.',
                      style: T.body(11, color: C.muted, height: 1.5),
                    ),
                    const SizedBox(height: 10),
                    Pressable(
                      onTap: () => Navigator.of(context).push(
                        PageRouteBuilder<void>(
                          pageBuilder: (context, animation, secondary) =>
                              const DiagnosticsScreen(),
                          transitionsBuilder: (context, animation, secondary, child) =>
                              FadeTransition(opacity: animation, child: child),
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
                            style: T.body(12, weight: 800, color: C.accent),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
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
class _TradingCard extends StatelessWidget {
  const _TradingCard({required this.trading});

  final TradingView trading;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.read(context);
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Торговый контур')),
              Builder(builder: (_) {
                final color = trading.killSwitch
                    ? C.red
                    : trading.ready
                        ? C.green
                        : C.muted;
                return OutlineBadge(
                  label: trading.killSwitch
                      ? 'ОСТАНОВЛЕН'
                      : trading.ready
                          ? 'ГОТОВ'
                          : 'НЕ ГОТОВ',
                  color: color,
                  borderColor: color,
                  fontWeight: 700,
                );
              }),
            ],
          ),
          const SizedBox(height: 8),

          if (!trading.vaultAvailable)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                'Защищённое хранилище на этом устройстве недоступно — ключи '
                'биржи сохранять некуда. Торговля отключена.',
                style: T.body(11.5, color: C.red, height: 1.4),
              ),
            ),

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
            KeyValueRow(
              name: 'Ключи',
              value: broker.hasKeys ? 'заданы · изменить' : 'не заданы · ввести',
              valueStyle: T.mono(12, color: broker.hasKeys ? C.green : C.accent),
              onTap: () => showBrokerKeysSheet(
                context,
                broker: BrokerId.parse(broker.id),
                mode: broker.live ? TradingMode.live : TradingMode.testnet,
                onSubmit: (key, secret) =>
                    controller.saveBrokerKeys(BrokerId.parse(broker.id), key, secret),
              ),
            ),
            if (!broker.liveAllowed)
              Padding(
                padding: const EdgeInsets.only(top: 2, bottom: 6),
                child: Text(
                  'Живой режим закрыт: ${broker.liveBlockedReason}.',
                  style: T.body(10.5, color: C.muted, height: 1.4),
                ),
              ),
          ],
          KeyValueRow(
            name: 'Подтверждение сделки',
            value: trading.biometricsAvailable ? 'биометрия' : 'недоступно',
            valueStyle: T.mono(
              12,
              color: trading.biometricsAvailable ? C.text : C.red,
            ),
          ),
          KeyValueRow(
            name: 'Допуск к живым деньгам',
            value: trading.gateAllowed ? 'открыт' : 'закрыт',
            valueStyle: T.mono(12, color: trading.gateAllowed ? C.green : C.muted),
          ),
          Padding(
            padding: const EdgeInsets.only(top: 2, bottom: 8),
            child: Text(trading.gateReason, style: T.body(11, color: C.muted, height: 1.4)),
          ),

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
            subtitle: 'Сопровождение открытых сделок и поиск новых идей раз в '
                'час. Ордера в фоне не отправляются: подтвердить сделку там нечем',
            value: background.enabled,
            onChanged: controller.setBackgroundEnabled,
          ),
          const SizedBox(height: 10),
          KeyValueRow(
            name: 'Режим',
            value: '${background.modeLabel} · сменить',
            valueStyle: T.mono(12, color: C.accent),
            onTap: () => controller.setBackgroundMode(
              background.persistent ? BackgroundMode.burst : BackgroundMode.persistent,
            ),
          ),
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
            background.persistent
                ? 'Постоянный режим держит в шторке служебную строку. На '
                    'Android 15+ система останавливает такой сервис после шести '
                    'часов в сутки — дальше контур продолжает часовыми '
                    'пробуждениями сам.'
                : 'Раз в час приложение просыпается на полминуты и снова '
                    'засыпает. Служебная строка видна только на это время.',
            style: T.body(10.5, color: C.muted, height: 1.4),
          ),
          const SizedBox(height: 6),
          Text(
            'Если Android усыпил приложение (на Samsung — «Спящие приложения» '
            'в настройках батареи), фон не поднимется ни в одном режиме. '
            'Исключите SignalAI из оптимизации.',
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
  });

  final String title;
  final List<ToggleSetting> items;
  final void Function(String id, bool enabled) onChanged;

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
          ],
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
