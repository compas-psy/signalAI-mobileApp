import 'dart:async';

import 'package:flutter/widgets.dart';

import '../../data/api/sandbox_mirror_delivery.dart';
import '../../data/api/server_capital.dart';
import '../../data/local_store.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/idea_funnel.dart';
import '../../domain/idea/paper_position.dart';
import '../../state/app_controller.dart';
import '../../state/app_scope.dart';
import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../formatters/capital_amount.dart';
import '../widgets/common.dart';
import 'ideas_screen.dart';

/// Production owner cockpit.
///
/// Capital comes from the server read model; trading lifecycle remains the
/// server PAPER ledger. T-Invest Sandbox proof is deliberately read from the
/// device-local durable delivery journal because the sandbox token never
/// leaves Android Keystore.
class ServerTodayScreen extends StatefulWidget {
  const ServerTodayScreen({super.key});

  @override
  State<ServerTodayScreen> createState() => _ServerTodayScreenState();
}

class _ServerTodayScreenState extends State<ServerTodayScreen> {
  late final ServerCapitalClient _capitalClient;
  ServerCapitalSnapshot? _capital;
  Object? _capitalError;
  bool _capitalLoading = true;
  Timer? _capitalTimer;

  @override
  void initState() {
    super.initState();
    _capitalClient = ServerCapitalClient();
    WidgetsBinding.instance.addPostFrameCallback((_) => _loadCapital());
    _capitalTimer = Timer.periodic(
      const Duration(minutes: 5),
      (_) => _loadCapital(silent: true),
    );
  }

  @override
  void dispose() {
    _capitalTimer?.cancel();
    _capitalClient.close();
    super.dispose();
  }

  Future<void> _loadCapital({bool silent = false}) async {
    if (!mounted) return;
    if (!silent) setState(() => _capitalLoading = true);
    try {
      final value = await _capitalClient.load();
      if (!mounted) return;
      setState(() {
        _capital = value;
        _capitalError = null;
        _capitalLoading = false;
      });
    } on Object catch (error) {
      if (!mounted) return;
      setState(() {
        _capitalError = error;
        _capitalLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final controller = AppScope.of(context);
    final now = DateTime.now();
    final funnel = IdeaFunnelSnapshot.from(
      ideas: controller.ideas,
      trades: controller.paperPositions,
      now: now,
    );

    return ListView(
      padding: const EdgeInsets.fromLTRB(S.screen, 12, S.screen, 90),
      children: [
        _ServerCapitalCard(
          snapshot: _capital,
          loading: _capitalLoading,
          error: _capitalError,
          onRefresh: _loadCapital,
        ),
        const SizedBox(height: 12),
        _DayStrip(controller: controller, funnel: funnel),
        if (funnel.open.isNotEmpty) ...[
          const SizedBox(height: 18),
          _TradeGroup(
            title: 'Позиции открыты',
            note: 'Вход исполнен. Позиции сопровождает сервер.',
            trades: funnel.open,
            ideas: controller.ideas,
            controller: controller,
          ),
        ],
        if (funnel.pending.isNotEmpty) ...[
          const SizedBox(height: 18),
          _TradeGroup(
            title: 'Ждут входа',
            note: 'Решение принято, но цена ещё не дошла до входа.',
            trades: funnel.pending,
            ideas: controller.ideas,
            controller: controller,
          ),
        ],
        if (funnel.decisions.isNotEmpty) ...[
          const SizedBox(height: 18),
          _IdeaGroup(
            title: 'Нужно решить',
            ideas: funnel.decisions,
            now: now,
            controller: controller,
          ),
        ],
        if (funnel.forming.isNotEmpty) ...[
          const SizedBox(height: 18),
          _IdeaGroup(
            title: 'Формируются',
            note: 'Кандидат уже есть, но вход не разрешён: сервер ждёт триггер.',
            ideas: funnel.forming.take(3).toList(),
            now: now,
            controller: controller,
          ),
        ],
        if (funnel.total > 0) ...[
          const SizedBox(height: 10),
          Align(
            alignment: Alignment.centerRight,
            child: Pressable(
              onTap: () {
                controller.goSection(AppSection.ideas);
                controller.goPill(IdeaFunnelPill.all.index);
              },
              child: Text(
                'Вся воронка · ${funnel.total} →',
                style: T.body(11.5, weight: 700, color: C.accent),
              ),
            ),
          ),
        ] else if (controller.ideasUnavailableReason != null) ...[
          const SizedBox(height: 18),
          SectionCard(
            child: Text(
              controller.ideasUnavailableReason!,
              style: T.body(11.5, color: C.warning, height: 1.5),
            ),
          ),
        ] else if (controller.noSetupsReason != null) ...[
          const SizedBox(height: 18),
          SectionCard(
            child: Text(
              controller.noSetupsReason!,
              style: T.body(11.5, color: C.muted, height: 1.5),
            ),
          ),
        ],
      ],
    );
  }
}

class _ServerCapitalCard extends StatelessWidget {
  const _ServerCapitalCard({
    required this.snapshot,
    required this.loading,
    required this.error,
    required this.onRefresh,
  });

  final ServerCapitalSnapshot? snapshot;
  final bool loading;
  final Object? error;
  final Future<void> Function({bool silent}) onRefresh;

  @override
  Widget build(BuildContext context) {
    final data = snapshot;
    return SectionCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(child: SectionLabel('Капитал')),
              Pressable(
                onTap: loading ? null : () => onRefresh(silent: false),
                child: Text(
                  loading ? 'сверяем…' : 'обновить ↻',
                  style: T.body(
                    10.5,
                    color: loading ? C.info : C.accent,
                    weight: 700,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (data == null) ...[
            Text(
              error == null
                  ? 'Получаем брокерский снимок с сервера…'
                  : 'Серверный снимок капитала недоступен. Последняя ошибка: $error',
              style: T.body(
                11.5,
                color: error == null ? C.muted : C.warning,
                height: 1.45,
              ),
            ),
          ] else ...[
            for (var i = 0; i < data.sources.length; i++) ...[
              _CapitalSourceRow(source: data.sources[i]),
              if (i + 1 < data.sources.length) const SizedBox(height: 10),
            ],
            if (data.incomplete) ...[
              const SizedBox(height: 9),
              Text(
                'Снимок частичный: недоступный источник не обнуляет последний успешный капитал.',
                style: T.body(10, color: C.warning, height: 1.4),
              ),
            ],
          ],
        ],
      ),
    );
  }
}

class _CapitalSourceRow extends StatelessWidget {
  const _CapitalSourceRow({required this.source});

  final ServerCapitalSource source;

  @override
  Widget build(BuildContext context) {
    final status = switch (source.status) {
      'fresh' => 'актуально',
      'stale' => 'последний снимок',
      'not_configured' => 'не настроено',
      _ => 'недоступно',
    };
    final tone = source.fresh ? C.green : (source.stale ? C.warning : C.muted);
    final totals = source.equityByCurrency.entries.toList();
    final value = totals.isEmpty
        ? '—'
        : totals
            .map((entry) => '${formatCapitalAmount(entry.value)} ${entry.key}')
            .join(' · ');
    final age = source.syncedAt == null ? '' : ' · ${_age(source.syncedAt!)}';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(source.title, style: T.body(12, weight: 700)),
            ),
            Text(value, style: T.mono(12, color: C.text)),
          ],
        ),
        const SizedBox(height: 3),
        Text(
          '$status$age${source.note.isEmpty ? '' : ' · ${source.note}'}',
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: T.body(9.5, color: tone, height: 1.35),
        ),
      ],
    );
  }


  static String _age(DateTime at) {
    final diff = DateTime.now().difference(at);
    if (diff.inMinutes < 1) return 'только что';
    if (diff.inHours < 1) return '${diff.inMinutes} мин назад';
    if (diff.inDays < 1) return '${diff.inHours} ч назад';
    return '${diff.inDays} дн назад';
  }
}

class _DayStrip extends StatelessWidget {
  const _DayStrip({required this.controller, required this.funnel});

  final AppController controller;
  final IdeaFunnelSnapshot funnel;

  @override
  Widget build(BuildContext context) => SectionCard(
        child: Row(
          children: [
            _Metric('Открыто', '${funnel.open.length}'),
            _Metric('Ждут входа', '${funnel.pending.length}'),
            _Metric('Решить', '${funnel.decisions.length}'),
            _Metric(
              'Риск/сделку',
              controller.risk == null
                  ? '—'
                  : '${controller.risk!.riskPercent.toStringAsFixed(2)}%',
            ),
          ],
        ),
      );
}

class _Metric extends StatelessWidget {
  const _Metric(this.label, this.value);

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => Expanded(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value, style: T.mono(13, color: C.text, weight: 700)),
            const SizedBox(height: 2),
            Text(label, style: T.body(8.5, color: C.muted)),
          ],
        ),
      );
}

class _TradeGroup extends StatelessWidget {
  const _TradeGroup({
    required this.title,
    required this.note,
    required this.trades,
    required this.ideas,
    required this.controller,
  });

  final String title;
  final String note;
  final List<PaperPosition> trades;
  final List<Idea> ideas;
  final AppController controller;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionLabel('$title · ${trades.length}'),
          const SizedBox(height: 4),
          Text(note, style: T.body(10.5, color: C.muted, height: 1.4)),
          const SizedBox(height: 8),
          for (final trade in trades) ...[
            _AuditedTradeCard(
              trade: trade,
              idea: ideas.where((idea) => idea.id == trade.ideaId).firstOrNull,
              onOpenIdea: trade.ideaId.isEmpty
                  ? null
                  : () => controller.openSignal(trade.ideaId),
            ),
            const SizedBox(height: 10),
          ],
        ],
      );
}

class _AuditedTradeCard extends StatelessWidget {
  const _AuditedTradeCard({
    required this.trade,
    required this.idea,
    required this.onOpenIdea,
  });

  final PaperPosition trade;
  final Idea? idea;
  final VoidCallback? onOpenIdea;

  @override
  Widget build(BuildContext context) {
    final forts = trade.isForts;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PaperPositionCard(trade: trade, idea: idea, onOpenIdea: onOpenIdea),
        const SizedBox(height: 6),
        SectionCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'SignalAI PAPER · ${_paperState(trade)}',
                style: T.body(10.5, weight: 700, color: C.green),
              ),
              const SizedBox(height: 3),
              Text(
                trade.lastReconciledAt == null
                    ? 'сервер ведёт lifecycle · сверка ещё не отмечена'
                    : 'серверная сверка ${_timestamp(trade.lastReconciledAt!)}',
                style: T.body(9.5, color: C.muted),
              ),
              if (forts) ...[
                const SizedBox(height: 8),
                _SandboxAudit(ideaId: trade.ideaId),
              ],
            ],
          ),
        ),
      ],
    );
  }

  static String _paperState(PaperPosition trade) => switch (trade.status) {
        PaperPositionStatus.pending => 'ждёт входа',
        PaperPositionStatus.open => 'позиция открыта',
        PaperPositionStatus.closed => 'закрыта',
        PaperPositionStatus.cancelled => 'отменена',
      };
}

class _SandboxAudit extends StatefulWidget {
  const _SandboxAudit({required this.ideaId});

  final String ideaId;

  @override
  State<_SandboxAudit> createState() => _SandboxAuditState();
}

class _SandboxAuditState extends State<_SandboxAudit> {
  late final SandboxMirrorDeliveryStore _store;
  SandboxMirrorDelivery? _delivery;

  @override
  void initState() {
    super.initState();
    _store = SandboxMirrorDeliveryStore(LocalStore());
    _reload();
  }

  Future<void> _reload() async {
    final delivery = await _store.load(widget.ideaId);
    if (mounted) setState(() => _delivery = delivery);
  }

  @override
  Widget build(BuildContext context) {
    final delivery = _delivery;
    if (delivery == null) {
      return Text(
        'T‑Invest Sandbox · подтверждения доставки на этом устройстве нет',
        style: T.body(9.5, color: C.warning, height: 1.4),
      );
    }

    final status = switch (delivery.status) {
      SandboxMirrorDeliveryStatus.completed => '✅ вход и защитный стоп приняты',
      SandboxMirrorDeliveryStatus.pending => '… доставка начата',
      SandboxMirrorDeliveryStatus.repairRequired => '⚠ требуется сверка',
      SandboxMirrorDeliveryStatus.notApplicable => 'не применяется',
    };
    final tone = delivery.status == SandboxMirrorDeliveryStatus.completed
        ? C.green
        : delivery.status == SandboxMirrorDeliveryStatus.repairRequired
            ? C.warning
            : C.muted;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'T‑Invest Sandbox · $status',
                style: T.body(9.5, weight: 700, color: tone),
              ),
            ),
            Pressable(
              onTap: _reload,
              child: Text('↻', style: T.body(11, color: C.accent)),
            ),
          ],
        ),
        if (delivery.exchangeOrderId.isNotEmpty) ...[
          const SizedBox(height: 3),
          Text(
            'broker order ${delivery.exchangeOrderId}',
            style: T.mono(8.8, color: C.muted),
          ),
        ],
        const SizedBox(height: 3),
        Text(
          delivery.protectionVerified
              ? 'защитный stop подтверждён ${_timestamp(delivery.protectiveStopVerifiedAt!)} · audit ${_timestamp(delivery.updatedAt)}'
              : 'последний audit ${_timestamp(delivery.updatedAt)}',
          style: T.body(9, color: C.muted, height: 1.35),
        ),
        if (delivery.lastError.isNotEmpty) ...[
          const SizedBox(height: 3),
          Text(
            delivery.lastError,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: T.body(9, color: C.warning, height: 1.35),
          ),
        ],
      ],
    );
  }
}

class _IdeaGroup extends StatelessWidget {
  const _IdeaGroup({
    required this.title,
    required this.ideas,
    required this.now,
    required this.controller,
    this.note,
  });

  final String title;
  final String? note;
  final List<Idea> ideas;
  final DateTime now;
  final AppController controller;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SectionLabel('$title · ${ideas.length}'),
          if (note != null) ...[
            const SizedBox(height: 4),
            Text(note!, style: T.body(10.5, color: C.muted, height: 1.4)),
          ],
          const SizedBox(height: 8),
          for (final idea in ideas) ...[
            IdeaCard(
              idea: idea,
              now: now,
              onTap: () => controller.openSignal(idea.id),
            ),
            const SizedBox(height: 10),
          ],
        ],
      );
}

String _timestamp(DateTime at) {
  final local = at.toLocal();
  String two(int value) => value.toString().padLeft(2, '0');
  return '${two(local.day)}.${two(local.month)} ${two(local.hour)}:${two(local.minute)}';
}
