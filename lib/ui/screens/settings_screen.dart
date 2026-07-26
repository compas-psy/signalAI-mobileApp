import 'package:flutter/widgets.dart';

import '../../core/format.dart';
import '../../domain/models/settings.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
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
