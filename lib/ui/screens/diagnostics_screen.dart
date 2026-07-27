import 'dart:io';

import 'package:flutter/widgets.dart';

import '../../data/market/bybit_client.dart';
import '../../data/market/http_json.dart';
import '../../data/net/dns_resolver.dart';
import '../../data/market/iss_client.dart';
import '../../domain/analysis/candle.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import '../widgets/common.dart';
import '../widgets/vector_icon.dart';
import 'raw_probe_screen.dart';

/// Диагностика данных: живой прогон обоих источников с вердиктами.
///
/// Клиенты бирж писались в окружении, где сеть к биржам закрыта, поэтому
/// разбор ответов нужно сверить с реальностью хотя бы один раз с устройства.
/// Этот экран делает сверку воспроизводимой: каждый чек показывает и сырые
/// значения, и вердикт — можно убедиться глазами, а не верить на слово.
class DiagnosticsScreen extends StatefulWidget {
  const DiagnosticsScreen({super.key});

  @override
  State<DiagnosticsScreen> createState() => _DiagnosticsScreenState();
}

class _CheckResult {
  const _CheckResult({required this.name, required this.ok, required this.details});

  final String name;
  final bool ok;
  final List<String> details;
}

class _DiagnosticsScreenState extends State<DiagnosticsScreen> {
  final List<_CheckResult> _results = [];
  bool _running = false;
  String? _stage;

  @override
  void initState() {
    super.initState();
    _run();
  }

  Future<void> _run() async {
    if (_running) return;
    setState(() {
      _running = true;
      _results.clear();
    });

    // Диагностика обязана падать быстро: её задача — поставить диагноз, а не
    // воспроизвести продовые ретраи. Короткий таймаут вместо дефолтных 15 с.
    final iss = IssClient(http: HttpJson(timeout: const Duration(seconds: 7)));
    final bybit = BybitClient(http: HttpJson(timeout: const Duration(seconds: 7)));

    Future<void> check(String name, Future<_CheckResult> Function() body) async {
      setState(() => _stage = name);
      _CheckResult result;
      try {
        result = await body();
      } on Exception catch (e) {
        result = _CheckResult(name: name, ok: false, details: ['ошибка: $e']);
      }
      setState(() => _results.add(result));
    }

    // ── Сначала связь устройства: без неё остальные чеки бессмысленны ──
    //
    // Три состояния различаются намеренно. Сокеты по литеральному IP не
    // требуют DNS: если они не проходят — сеть закрыта самому приложению, и
    // чинить надо телефон. Если сокеты есть, а имена не резолвятся — сломан
    // DNS, и приложение обойдёт его само.
    var socketsWork = false;
    var systemDnsWorks = false;

    await check('Связь устройства: сокеты наружу', () async {
      try {
        final socket = await Socket.connect(
          InternetAddress('1.1.1.1'),
          443,
          timeout: const Duration(seconds: 8),
        );
        socket.destroy();
        socketsWork = true;
        return const _CheckResult(
          name: 'Связь устройства: сокеты наружу',
          ok: true,
          details: [
            'соединение на 1.1.1.1:443 установлено (DNS для этого не нужен)',
            'сеть приложению доступна',
          ],
        );
      } on Object catch (e) {
        return _CheckResult(
          name: 'Связь устройства: сокеты наружу',
          ok: false,
          details: [
            'не удалось соединиться с 1.1.1.1:443 — $e',
            'СЕТЬ ЗАКРЫТА САМОМУ ПРИЛОЖЕНИЮ. Что проверить на Samsung:',
            '1) Настройки → Приложения → SignalAI → Мобильные данные — включить '
                '«Разрешить использование фоновых данных» и «Разрешить при '
                'включённой экономии трафика»',
            '2) Настройки → Приложения → SignalAI → Мобильные данные → Wi-Fi — разрешить',
            '3) отключить активный VPN, если он включён',
            '4) Настройки → Подключения → Использование данных → Экономия трафика — выключить',
          ],
        );
      }
    });

    await check('Связь устройства: системный DNS', () async {
      try {
        final result = await InternetAddress.lookup('example.com')
            .timeout(const Duration(seconds: 8));
        systemDnsWorks = result.isNotEmpty;
        return _CheckResult(
          name: 'Связь устройства: системный DNS',
          ok: systemDnsWorks,
          details: ['example.com → ${result.map((a) => a.address).join(', ')}'],
        );
      } on Object catch (e) {
        return _CheckResult(
          name: 'Связь устройства: системный DNS',
          ok: false,
          details: [
            'резолв не прошёл — $e',
            if (socketsWork)
              'Сокеты работают, а имена не резолвятся — сломан DNS. Приложение '
                  'включит обходной резолвер (DoH) само, следующий чек это покажет.'
            else
              'Скорее всего следствие закрытой сети (см. чек выше).',
            'Проверить: Настройки → Подключения → Другие настройки → Частный DNS '
                '— поставить «Выключено» или «Автоматически».',
          ],
        );
      }
    });

    await check('Связь устройства: обходной резолвер (DoH)', () async {
      final resolver = DnsResolver();
      try {
        final addresses = await resolver.resolve('iss.moex.com');
        return _CheckResult(
          name: 'Связь устройства: обходной резолвер (DoH)',
          ok: addresses.isNotEmpty,
          details: [
            if (addresses.isEmpty)
              'обходной резолвер тоже не смог получить адрес'
            else ...[
              'iss.moex.com → ${addresses.map((a) => a.address).join(', ')}',
              systemDnsWorks
                  ? 'системный DNS работает — обход не понадобится'
                  : 'системный DNS сломан, но обход работает: приложение будет '
                      'ходить на биржи через него, проверка сертификата при этом '
                      'остаётся полной',
            ],
          ],
        );
      } finally {
        resolver.close();
      }
    });

    String? fortsSymbol;

    await check('MOEX ISS: снимок срочного рынка', () async {
      final snapshots = await iss.fortsSnapshot();
      final withPrice = snapshots.where((s) => s.lastPrice > 0).toList();
      final si = withPrice.where((s) => s.spec.symbol.toUpperCase().startsWith('SI')).toList()
        ..sort((a, b) => b.turnover.compareTo(a.turnover));
      fortsSymbol = si.isEmpty ? (withPrice.isEmpty ? null : withPrice.first.spec.symbol) : si.first.spec.symbol;
      final sample = withPrice.isEmpty ? null : withPrice.first;
      return _CheckResult(
        name: 'MOEX ISS: снимок срочного рынка',
        ok: snapshots.length > 50 && withPrice.isNotEmpty,
        details: [
          'контрактов: ${snapshots.length} (ожидаем > 50 — иначе пагинация не работает)',
          'с ценой: ${withPrice.length}',
          if (sample != null)
            'пример: ${sample.spec.symbol} · цена ${sample.lastPrice} · '
                'оборот ${sample.turnover.toStringAsFixed(0)} · '
                'обновлено ${sample.updatedAt?.toIso8601String() ?? '—'}',
          if (fortsSymbol != null) 'дальше проверяем на: $fortsSymbol',
        ],
      );
    });

    await check('MOEX ISS: часовые свечи', () async {
      final symbol = fortsSymbol;
      if (symbol == null) {
        return const _CheckResult(
          name: 'MOEX ISS: часовые свечи',
          ok: false,
          details: ['нет инструмента из предыдущего шага'],
        );
      }
      final candles = await iss.candles(
        symbol,
        timeframe: Timeframe.h1,
        from: DateTime.now().subtract(const Duration(days: 14)),
      );
      final ascending = _isAscending(candles);
      return _CheckResult(
        name: 'MOEX ISS: часовые свечи',
        ok: candles.length > 40 && ascending,
        details: [
          'свечей за 14 дней: ${candles.length} (ожидаем > 40)',
          'порядок от старых к новым: ${ascending ? 'да' : 'НЕТ — разбор сломан'}',
          if (candles.isNotEmpty)
            'последняя: ${candles.last.time.toIso8601String()} · '
                'O${candles.last.open} H${candles.last.high} '
                'L${candles.last.low} C${candles.last.close}',
        ],
      );
    });

    await check('MOEX ISS: пагинация истории (курсор start)', () async {
      final symbol = fortsSymbol;
      if (symbol == null) {
        return const _CheckResult(
          name: 'MOEX ISS: пагинация истории (курсор start)',
          ok: false,
          details: ['нет инструмента'],
        );
      }
      final candles = await iss.candles(
        symbol,
        timeframe: Timeframe.h1,
        from: DateTime.now().subtract(const Duration(days: 60)),
      );
      // Одна страница ISS — 500 строк: больше 500 значит курсор двигает выдачу.
      return _CheckResult(
        name: 'MOEX ISS: пагинация истории (курсор start)',
        ok: candles.length > 500,
        details: [
          'свечей за 60 дней: ${candles.length}',
          candles.length > 500
              ? 'больше одной страницы (500) — курсор работает'
              : 'НЕ больше 500 — курсор, возможно, игнорируется',
        ],
      );
    });

    await check('Bybit: тикеры и фандинг', () async {
      final tickers = await bybit.tickers(symbols: const ['BTCUSDT']);
      final btc = tickers.isEmpty ? null : tickers.first;
      final saneChange = btc != null && btc.changePercent.abs() < 30;
      return _CheckResult(
        name: 'Bybit: тикеры и фандинг',
        ok: btc != null && btc.lastPrice > 0 && saneChange,
        details: [
          if (btc == null) 'BTCUSDT не найден'
          else ...[
            'BTCUSDT: цена ${btc.lastPrice}',
            'изменение за сутки: ${btc.changePercent.toStringAsFixed(2)}% '
                '(|x| < 30 — шкала price24hPcnt разобрана верно)',
            'фандинг: ${btc.fundingRate == null ? 'НЕ разобран' : btc.fundingRate!.toStringAsFixed(6)}',
            'открытый интерес: ${btc.openInterest}',
          ],
        ],
      );
    });

    await check('Bybit: свечи (порядок и сходимость с тикером)', () async {
      final candles = await bybit.candles('BTCUSDT', timeframe: Timeframe.h1);
      final tickers = await bybit.tickers(symbols: const ['BTCUSDT']);
      final ascending = _isAscending(candles);
      final last = candles.isEmpty ? 0.0 : candles.last.close;
      final ticker = tickers.isEmpty ? 0.0 : tickers.first.lastPrice;
      final converges = ticker > 0 && (last - ticker).abs() / ticker < 0.02;
      return _CheckResult(
        name: 'Bybit: свечи (порядок и сходимость с тикером)',
        ok: candles.length > 100 && ascending && converges,
        details: [
          'свечей: ${candles.length}',
          'порядок от старых к новым: ${ascending ? 'да (разворот работает)' : 'НЕТ'}',
          'закрытие последней: $last · тикер: $ticker · '
              'расходимость ${ticker == 0 ? '—' : '${((last - ticker).abs() / ticker * 100).toStringAsFixed(2)}%'} '
              '(< 2% — свечи те и свежие)',
        ],
      );
    });

    await check('Bybit: история открытого интереса', () async {
      final oi = await bybit.openInterestHistory('BTCUSDT');
      return _CheckResult(
        name: 'Bybit: история открытого интереса',
        ok: oi.length >= 24 && oi.every((v) => v > 0),
        details: [
          'точек: ${oi.length} (ожидаем ≥ 24)',
          if (oi.isNotEmpty) 'последняя: ${oi.last}',
        ],
      );
    });

    await check('Защита от подмены таймфрейма', () async {
      var threw = false;
      try {
        await bybit.candles('BTCUSDT', timeframe: Timeframe.m10);
      } on ArgumentError {
        threw = true;
      }
      return _CheckResult(
        name: 'Защита от подмены таймфрейма',
        ok: threw,
        details: [
          threw
              ? 'Bybit честно отказал в 10-минутках (ArgumentError) — тихой подмены нет'
              : 'ОШИБКА: Bybit отдал 10-минутки, которых у него нет',
        ],
      );
    });

    // Цепочка опционов идёт последней сознательно: доска на порядок длиннее
    // остальных выдач, и раньше экран стоял на ней, не доходя до следующих
    // строк. Теперь всё остальное уже показано, а этот прогон ещё и печатает
    // протокол запроса — что именно спрашивали и что пришло.
    await check('MOEX ISS: цепочка опционов', () async {
      const asset = 'SI';
      final probe = await iss.optionsChainProbe(
        asset,
        ttl: Duration.zero,
        budget: const Duration(seconds: 20),
      );
      final chain = probe.rows;
      final protocol = [
        'путь: ${probe.note}, запросов ${probe.requests}',
        for (final report in probe.reports.take(2))
          report.ok
              ? '${report.url.path}: строк ${report.rows} '
                  'за ${report.elapsed.inMilliseconds} мс'
              : 'ОШИБКА ${report.url.path}: ${report.error}',
        for (final entry in (probe.reports.isEmpty
                ? const <String, IssBlockShape>{}
                : probe.reports.first.blocks)
            .entries)
          entry.value.present
              ? '${entry.key}: ${entry.value.columns.join(', ')}'
              : 'блока ${entry.key} в ответе нет',
      ];

      if (chain.isEmpty) {
        return _CheckResult(
          name: 'MOEX ISS: цепочка опционов',
          ok: false,
          details: [
            'по $asset не пришло ни одного контракта',
            ...protocol,
            'откройте «Сырой ответ биржи» — там видно, что отдаёт ISS',
          ],
        );
      }
      final withPrice = chain.where((c) => (c.last ?? c.theoretical ?? 0) > 0).length;
      final withIv = chain.where((c) => (c.impliedVolatility ?? 0) > 0).length;
      final withOi = chain.where((c) => c.openInterest > 0).length;
      final strikes = {for (final c in chain) c.strike}.length;
      final expiries = {for (final c in chain) c.expiration}.length;

      return _CheckResult(
        name: 'MOEX ISS: цепочка опционов',
        // Цена обязательна: без неё конструкцию не собрать. IV и открытый
        // интерес желательны — их отсутствие ухудшает оценку, но не ломает.
        ok: withPrice > 0 && strikes > 3,
        details: [
          'контрактов по $asset: ${chain.length}',
          'страйков: $strikes, серий: $expiries',
          'с ценой: $withPrice',
          withIv > 0
              ? 'с волатильностью биржи: $withIv'
              : 'ВОЛАТИЛЬНОСТЬ НЕ ПРИШЛА — считаем сами из цены',
          withOi > 0
              ? 'с открытым интересом: $withOi'
              : 'ОТКРЫТЫЙ ИНТЕРЕС НЕ ПРИШЁЛ — ликвидность не проверить',
          'пример: ${chain.first.secId} страйк ${chain.first.strike}',
          ...protocol,
        ],
      );
    });

    setState(() {
      _running = false;
      _stage = null;
    });
  }

  static bool _isAscending(List<Candle> candles) {
    for (var i = 1; i < candles.length; i++) {
      if (candles[i].time.isBefore(candles[i - 1].time)) return false;
    }
    return candles.isNotEmpty;
  }

  @override
  Widget build(BuildContext context) {
    final passed = _results.where((r) => r.ok).length;
    return ColoredBox(
      color: C.bg,
      child: SafeArea(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              decoration: const BoxDecoration(
                color: C.headerBg,
                border: Border(bottom: BorderSide(color: C.dividerSoft)),
              ),
              child: Row(
                children: [
                  Pressable(
                    onTap: () => Navigator.of(context).pop(),
                    child: Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: C.card,
                        shape: BoxShape.circle,
                        border: Border.all(color: C.border),
                      ),
                      child: const Center(
                        child: VectorIcon(Icons.chevronLeft, size: 18, color: C.text),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('Диагностика данных', style: T.jost(18)),
                        Text(
                          _running
                              ? (_stage ?? 'запускаем…')
                              : 'пройдено $passed из ${_results.length}',
                          style: T.body(11, color: _running ? C.accent : C.muted),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.all(S.screen),
                children: [
                  Text(
                    'Живой прогон MOEX ISS и Bybit: каждый чек показывает сырые '
                    'значения и вердикт. Если всё зелёное — разбор данных '
                    'соответствует реальности бирж, и скринеру можно верить '
                    'на уровне данных.',
                    style: T.body(11, color: C.muted, height: 1.5),
                  ),
                  const SizedBox(height: 10),
                  ActionButton(
                    label: 'Сырой ответ биржи',
                    dense: true,
                    onTap: () => Navigator.of(context).push(
                      PageRouteBuilder<void>(
                        pageBuilder: (_, _, _) => const RawProbeScreen(),
                        transitionsBuilder: (_, animation, _, child) =>
                            FadeTransition(opacity: animation, child: child),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  for (final result in _results) ...[
                    _CheckCard(result: result),
                    const SizedBox(height: 8),
                  ],
                  if (_running)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 16),
                      child: Center(
                        child: Text(_stage ?? '…', style: T.mono(12, color: C.accent)),
                      ),
                    ),
                  if (!_running) ...[
                    const SizedBox(height: 8),
                    Pressable(
                      onTap: _run,
                      child: Container(
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          border: Border.all(color: C.borderHover),
                          borderRadius: BorderRadius.circular(R.inner),
                        ),
                        child: Center(
                          child: Text('Запустить снова',
                              style: T.body(13, weight: 800, color: C.accent)),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _CheckCard extends StatelessWidget {
  const _CheckCard({required this.result});

  final _CheckResult result;

  @override
  Widget build(BuildContext context) => SectionCard(
        margin: EdgeInsets.zero,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: result.ok ? C.green : C.red,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(child: Text(result.name, style: T.body(12.5, weight: 700))),
              ],
            ),
            const SizedBox(height: 6),
            for (final line in result.details)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text('· $line', style: T.mono(10.5, color: C.muted, height: 1.4)),
              ),
          ],
        ),
      );
}
