import 'package:flutter/material.dart';

import '../../data/api/integrations_client.dart';
import '../../state/app_scope.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/engine_address_sheet.dart';

/// Интеграции thin-клиента. Секреты вводятся на телефоне, но хранятся
/// только в server-side vault; обратно приложение получает лишь статус.
class ServerIntegrationsScreen extends StatefulWidget {
  const ServerIntegrationsScreen({super.key});

  @override
  State<ServerIntegrationsScreen> createState() => _ServerIntegrationsScreenState();
}

class _ServerIntegrationsScreenState extends State<ServerIntegrationsScreen> {
  final IntegrationsClient _api = IntegrationsClient();
  List<ServerIntegration>? _items;
  String? _error;
  String? _busySlot;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _reload());
  }

  Future<void> _reload() async {
    final controller = AppScope.read(context);
    if (!controller.engineTokenSet || controller.engineBaseUrl.isEmpty) {
      setState(() {
        _items = null;
        _error = controller.engineAuthIssue ?? 'Сначала привяжите устройство к серверу.';
      });
      return;
    }
    try {
      final items = await _api.list();
      if (!mounted) return;
      setState(() {
        _items = items;
        _error = null;
      });
    } catch (error) {
      if (mounted) setState(() => _error = '$error');
    }
  }

  Future<void> _edit(ServerIntegration item) async {
    final values = await _showSecretSheet(context, item);
    if (values == null || !mounted) return;
    setState(() => _busySlot = item.slot);
    try {
      final updated = await _api.save(item.slot, values);
      if (!mounted) return;
      setState(() {
        _items = [
          for (final current in _items ?? const <ServerIntegration>[])
            if (current.slot == item.slot) updated else current,
        ];
        _error = null;
      });
      AppScope.read(context).showToast('${item.title}: секрет сохранён на сервере');
    } catch (error) {
      if (mounted) AppScope.read(context).showError(error);
    } finally {
      if (mounted) setState(() => _busySlot = null);
    }
  }

  Future<void> _remove(ServerIntegration item) async {
    setState(() => _busySlot = item.slot);
    try {
      await _api.remove(item.slot);
      await _reload();
      if (mounted) AppScope.read(context).showToast('${item.title}: секрет удалён');
    } catch (error) {
      if (mounted) AppScope.read(context).showError(error);
    } finally {
      if (mounted) setState(() => _busySlot = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final items = _items;
    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _EngineConnectionCard(onSaved: _reload),
        const SizedBox(height: S.gap),
        if (_error != null)
          SectionCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const SectionLabel('Связь с сервером'),
                const SizedBox(height: 6),
                Text(_error!, style: T.body(11.5, color: C.warning, height: 1.5)),
                const SizedBox(height: 10),
                ActionButton(label: 'Повторить', dense: true, onTap: _reload),
              ],
            ),
          ),
        if (items == null && _error == null)
          const Padding(
            padding: EdgeInsets.all(20),
            child: BusyLine(label: 'Читаем состояние интеграций…'),
          ),
        if (items != null) ...[
          _VenueGroup(
            title: 'Т-Инвестиции',
            note: 'Песочница используется для проверки исполнения без денег; '
                'read-only — для портфеля и метаданных; торговый токен — для '
                'будущего live-контура. Само наличие trade-token live не включает.',
            items: items.where((e) => e.venue == 'TINVEST').toList(),
            busySlot: _busySlot,
            onEdit: _edit,
            onRemove: _remove,
          ),
          const SizedBox(height: S.gap),
          _VenueGroup(
            title: 'Bybit',
            note: 'Read-only и live trade разделены. Bybit Testnet необязателен: '
                'проверка стратегии идёт через server-side paper на реальных '
                'котировках. У live-ключа право Withdraw должно быть выключено.',
            items: items.where((e) => e.venue == 'BYBIT').toList(),
            busySlot: _busySlot,
            onEdit: _edit,
            onRemove: _remove,
          ),
        ],
        const SizedBox(height: S.gap),
        SectionCard(
          child: Text(
            controller.thinMode
                ? 'Телефон не хранит брокерские ключи и не исполняет ордера сам. '
                    'Сервер считает, хранит секреты и сопровождает подтверждённые '
                    'paper-сделки; live execution открывается отдельным gate.'
                : 'Этот экран предназначен для thin-клиента.',
            style: T.body(10.5, color: C.faint, height: 1.5),
          ),
        ),
      ],
    );
  }
}

class _EngineConnectionCard extends StatelessWidget {
  const _EngineConnectionCard({required this.onSaved});
  final Future<void> Function() onSaved;

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final ready = controller.engineTokenSet && controller.engineBaseUrl.isNotEmpty;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Движок SignalAI')),
              OutlineBadge(
                label: ready ? 'ПРИВЯЗАН' : 'НУЖНА ПРИВЯЗКА',
                color: ready ? C.green : C.warning,
                borderColor: ready ? C.greenBorder : C.warning,
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            controller.engineBaseUrl.isEmpty ? 'адрес не задан' : controller.engineBaseUrl,
            style: T.mono(12, weight: 600),
          ),
          if (controller.engineAuthIssue != null) ...[
            const SizedBox(height: 6),
            Text(controller.engineAuthIssue!, style: T.body(11, color: C.warning, height: 1.45)),
          ],
          const SizedBox(height: 10),
          ActionButton(
            label: ready ? 'Перепривязать / сменить адрес' : 'Привязать устройство',
            dense: true,
            primary: !ready,
            onTap: () => showEngineAddressSheet(
              context,
              current: controller.engineBaseUrl,
              currentToken: '',
              onSubmit: (url, token) async {
                await controller.setEngineBaseUrl(url, token: token);
                await onSaved();
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _VenueGroup extends StatelessWidget {
  const _VenueGroup({
    required this.title,
    required this.note,
    required this.items,
    required this.busySlot,
    required this.onEdit,
    required this.onRemove,
  });

  final String title;
  final String note;
  final List<ServerIntegration> items;
  final String? busySlot;
  final Future<void> Function(ServerIntegration) onEdit;
  final Future<void> Function(ServerIntegration) onRemove;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SectionLabel(title),
            const SizedBox(height: 6),
            Text(note, style: T.body(11, color: C.muted, height: 1.5)),
            const SizedBox(height: 10),
            for (var index = 0; index < items.length; index++) ...[
              _IntegrationRow(
                item: items[index],
                busy: busySlot == items[index].slot,
                onEdit: () => onEdit(items[index]),
                onRemove: items[index].configured ? () => onRemove(items[index]) : null,
              ),
              if (index + 1 < items.length) const SizedBox(height: 8),
            ],
          ],
        ),
      );
}

class _IntegrationRow extends StatelessWidget {
  const _IntegrationRow({
    required this.item,
    required this.busy,
    required this.onEdit,
    required this.onRemove,
  });

  final ServerIntegration item;
  final bool busy;
  final VoidCallback onEdit;
  final VoidCallback? onRemove;

  @override
  Widget build(BuildContext context) {
    final status = item.configured
        ? 'ЗАДАН'
        : item.required
            ? 'НЕ ЗАДАН'
            : 'ОПЦИОНАЛЬНО';
    final statusColor = item.configured
        ? C.green
        : item.required
            ? C.warning
            : C.muted;
    return InsetBox(
      padding: const EdgeInsets.all(11),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(child: Text(item.title, style: T.body(12.5, weight: 700))),
              Text(status, style: T.mono(10, color: statusColor)),
            ],
          ),
          const SizedBox(height: 3),
          Text(item.purpose, style: T.body(10.5, color: C.muted, height: 1.4)),
          const SizedBox(height: 8),
          if (busy)
            const BusyLine(label: 'Сохраняем на сервере…')
          else
            Row(
              children: [
                Expanded(
                  child: ActionButton(
                    label: item.configured ? 'Заменить' : item.required ? 'Ввести' : 'Настроить при желании',
                    dense: true,
                    onTap: onEdit,
                  ),
                ),
                if (onRemove != null) ...[
                  const SizedBox(width: 8),
                  Expanded(
                    child: ActionButton(label: 'Удалить', dense: true, onTap: onRemove),
                  ),
                ],
              ],
            ),
        ],
      ),
    );
  }
}

Future<Map<String, String>?> _showSecretSheet(
  BuildContext context,
  ServerIntegration item,
) async {
  final controllers = {
    for (final field in item.fields) field: TextEditingController(),
  };
  try {
    return await showModalBottomSheet<Map<String, String>>(
      context: context,
      isScrollControlled: true,
      backgroundColor: C.sheet,
      builder: (context) => Padding(
        padding: EdgeInsets.fromLTRB(
          18,
          16,
          18,
          18 + MediaQuery.of(context).viewInsets.bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(item.title, style: T.jost(18)),
            const SizedBox(height: 4),
            Text(
              'Значение уйдёт по HTTPS в server-side vault. После сохранения '
              'приложение увидит только статус, а не сам секрет.',
              style: T.body(11, color: C.muted, height: 1.45),
            ),
            const SizedBox(height: 12),
            for (final field in item.fields) ...[
              Text(_fieldLabel(field), style: T.body(11, weight: 700, color: C.faint)),
              const SizedBox(height: 4),
              TextField(
                controller: controllers[field],
                obscureText: true,
                autocorrect: false,
                enableSuggestions: false,
                style: T.mono(12),
                decoration: InputDecoration(
                  filled: true,
                  fillColor: C.inset,
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(R.inner),
                    borderSide: const BorderSide(color: C.border),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(R.inner),
                    borderSide: const BorderSide(color: C.border),
                  ),
                ),
              ),
              const SizedBox(height: 10),
            ],
            Row(
              children: [
                Expanded(child: ActionButton(label: 'Отмена', onTap: () => Navigator.of(context).pop())),
                const SizedBox(width: 8),
                Expanded(
                  child: ActionButton(
                    label: 'Сохранить',
                    primary: true,
                    onTap: () {
                      final values = {
                        for (final entry in controllers.entries) entry.key: entry.value.text.trim(),
                      };
                      if (values.values.any((value) => value.isEmpty)) return;
                      Navigator.of(context).pop(values);
                    },
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  } finally {
    for (final controller in controllers.values) {
      controller.dispose();
    }
  }
}

String _fieldLabel(String field) => switch (field) {
      'token' => 'Токен',
      'api_key' => 'API Key',
      'api_secret' => 'API Secret',
      _ => field,
    };
