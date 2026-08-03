import '../../domain/models/signal.dart';
import '../../domain/idea/idea.dart';
import '../../domain/idea/paper_position.dart';
import '../../domain/portfolio/package.dart';
import '../../domain/research/hypothesis.dart';
import 'api_client.dart';
import 'engine_contract.dart';

/// Клиент движка по контракту §18.
///
/// Отдельный от [ApiClient] слой существует ровно затем, чтобы разбор
/// контракта был в одном месте и проверялся тестами. Экраны получают готовые
/// доменные объекты и ничего не знают ни о путях, ни о форме JSON.
///
/// Отказ здесь — **состояние**, а не исключение, которое кто-то проглотит.
/// Пустой список идей и недоступный сервер выглядят на экране одинаково, если
/// разницу не передать явно; ТЗ §24 прямо требует показывать деградацию, а не
/// маскировать её под «сегодня сетапов нет».
class EngineIdeas {
  const EngineIdeas({
    required this.ideas,
    this.unavailableReason,
    this.noSetupsReason,
  });

  const EngineIdeas.unavailable(String reason)
      : ideas = const [],
        unavailableReason = reason,
        noSetupsReason = null;

  final List<Idea> ideas;

  /// Почему движок недоступен. null — движок ответил.
  final String? unavailableReason;

  /// Почему сетапов нет, если движок ответил пустым списком.
  final String? noSetupsReason;

  bool get isAvailable => unavailableReason == null;
}

class EngineClient {
  EngineClient({ApiClient? client}) : _api = client ?? ApiClient();

  final ApiClient _api;

  static const _base = '/api/v1';

  /// Адреса движка нет — и это чинится в приложении, без пересборки.
  ///
  /// Прежний текст сообщал диагноз («не задан в сборке») и молчал о
  /// лечении. Владелец при этом упирался в пустой экран, хотя поле адреса
  /// лежит через два касания. Причина без выхода из неё — половина ответа.
  static const _noAddress =
      'Адрес движка не задан. Идеи и пакеты считает сервер — задайте его '
      'адрес в «Настройках» → «Подключения» → «Задать адрес».';

  bool get isConfigured => _api.baseUrl.isNotEmpty;

  /// Карточки дня (§16): не больше трёх, с причиной, если торговать нечего.
  Future<EngineIdeas> today() async {
    if (!isConfigured) {
      return const EngineIdeas.unavailable(_noAddress);
    }
    try {
      final json = await _api.get('$_base/ideas/today');
      // Два списка, а не один: §16 разделяет «торговать сейчас» и «ждать
      // триггера». Слить их значило бы выдать наблюдение за готовую сделку.
      final ideas = <Idea>[
        ..._parse(json['trade_now']),
        ..._parse(json['wait_for_trigger']),
      ];
      return EngineIdeas(
        ideas: ideas,
        noSetupsReason: ideas.isEmpty
            ? (json['no_trade_reason'] as String? ??
                'Движок отработал и не нашёл сетапов, проходящих допуск §16.')
            : null,
      );
    } catch (error) {
      return EngineIdeas.unavailable(_reason(error));
    }
  }

  static List<Idea> _parse(Object? raw) {
    if (raw is! List) return const [];
    return [
      for (final item in raw)
        if (item is Map<String, dynamic>) EngineContract.idea(item),
    ];
  }

  /// Полная карточка с планом, разбором оценки, доказательствами и разметкой.
  Future<Idea?> detail(String id) async {
    if (!isConfigured) return null;
    try {
      final json = await _api.get('$_base/ideas/$id');
      return EngineContract.idea(json);
    } catch (_) {
      // Сводка из ленты уже на экране; упавшая догрузка полной карточки не
      // должна ронять разбор — просто останется меньше подробностей.
      return null;
    }
  }

  /// Лента идей, включая непоказанные (§12): журнал обязан отвечать на
  /// вопрос, что система нашла, но не показала.
  Future<EngineIdeas> feed({int limit = 50}) async {
    if (!isConfigured) {
      return const EngineIdeas.unavailable(_noAddress);
    }
    try {
      // Лента отдаётся массивом верхнего уровня — оборачиваем его на
      // стороне клиента, чтобы разбор был один и тот же для обеих форм.
      return EngineIdeas(
        ideas: _parse(await _api.getList('$_base/ideas?limit=$limit')),
      );
    } catch (error) {
      return EngineIdeas.unavailable(_reason(error));
    }
  }

  /// Свечи инструмента (§23, `GET /api/v1/market/{id}/bars`).
  ///
  /// До этого график идеи не наполнялся ничем: свечи умел строить только
  /// скринер на устройстве, а идеи приходят с движка — и разбор всегда
  /// показывал заглушку «живой график недоступен». Два конца одной картинки
  /// были подключены к разным источникам.
  ///
  /// Цены приходят строками намеренно (`Money` в схемах сервера): JSON-число
  /// это double, и шаг цены 0,005 на другом конце становится
  /// 0,004999999999999999. Разбираем строку, а не число.
  ///
  /// `closed_only` не трогаем — сервер по умолчанию отдаёт только закрытые
  /// бары (§4.4). Незакрытый бар на графике живёт своей жизнью между двумя
  /// запросами, и разметка по нему уезжает.
  Future<SignalChart?> bars(
    String instrumentId, {
    required String timeframe,
    int limit = 200,
  }) async {
    if (!isConfigured) return null;
    try {
      final raw = await _api.getList(
        '$_base/market/$instrumentId/bars?timeframe=$timeframe&limit=$limit',
      );
      final candles = <ChartCandle>[];
      for (final item in raw) {
        if (item is! Map<String, dynamic>) continue;
        final open = _price(item['open']);
        final high = _price(item['high']);
        final low = _price(item['low']);
        final close = _price(item['close']);
        // Свеча без полной цены не рисуется вовсе: дорисованный нулём бар
        // выглядит как обвал, которого не было.
        if (open == null || high == null || low == null || close == null) {
          continue;
        }
        candles.add(ChartCandle(
          open,
          high,
          low,
          close,
          DateTime.tryParse('${item['open_time']}')?.toLocal(),
        ));
      }
      if (candles.isEmpty) return null;
      return SignalChart(timeframeLabel: timeframe, candles: candles);
    } catch (_) {
      // Молча: график — не то, ради чего стоит рушить разбор идеи. Его
      // отсутствие видно на самом графике, и там же написана причина.
      return null;
    }
  }

  /// Пакеты капитала (§6, `GET /api/v1/portfolio/packages`).
  ///
  /// Ответ несёт и состав, и состояние конвейера. Второе не менее важно:
  /// пустой список пакетов сам по себе не отвечает на вопрос «почему», а
  /// именно его владелец и задаёт, открывая пустой экран.
  Future<PortfolioState> portfolio() async {
    if (!isConfigured) {
      return const PortfolioState.unavailable(_noAddress);
    }
    try {
      return EngineContract.portfolio(
          await _api.get('$_base/portfolio/packages'));
    } catch (error) {
      return PortfolioState.unavailable(_reason(error));
    }
  }

  /// Ранние сигналы: гипотезы и готовность источников.
  ///
  /// Отдельно от идей и от пакетов, потому что это третий вопрос. Идеи —
  /// когда входить, пакеты — как разложить капитал, гипотезы — что
  /// изменилось в экономике эмитента и стоит ли это изучать. Исполнять
  /// гипотезу нечем, и подтверждать кнопкой тоже.
  Future<ResearchState> research() async {
    if (!isConfigured) {
      return const ResearchState.unavailable(_noAddress);
    }
    try {
      return ResearchState.fromJson(
        await _api.get('$_base/research/hypotheses'),
      );
    } catch (error) {
      return ResearchState.unavailable(_reason(error));
    }
  }

  /// Отправить движку снимок позиций инвестиционного счёта.
  ///
  /// Счёт читает устройство, а не сервер, и это не разделение труда, а
  /// граница безопасности. Токен Т-Инвестиций на чтение лежит в защищённом
  /// хранилище телефона; он привязан к пользователю, а не к счёту, и видит
  /// **все** счета владельца. Класть такой токен на VPS значило бы сложить
  /// весь капитал в одну точку отказа ради удобства синхронизации.
  ///
  /// Сервер по снимку считает расхождение с целевым составом и предлагает,
  /// что поправить. Заявок по этому предложению не отправляет никто.
  ///
  /// Возвращает ответ движка или null, если отправить не удалось. Отказ —
  /// не исключение: снимок посылается фоном, и ронять из-за него чтение
  /// счёта нельзя.
  Future<Map<String, dynamic>?> putHoldings({
    required String accountId,
    required List<Map<String, dynamic>> positions,
    String broker = 'tinvest',
    String title = '',
    String? equity,
  }) async {
    if (!isConfigured || accountId.isEmpty) return null;
    try {
      return await _api.post('$_base/portfolio/holdings', body: {
        'broker': broker,
        'account_id': accountId,
        if (title.isNotEmpty) 'title': title,
        'equity': ?equity,
        'positions': positions,
      });
    } catch (_) {
      return null;
    }
  }

  static double? _price(Object? raw) => switch (raw) {
        num n => n.toDouble(),
        String s => double.tryParse(s),
        _ => null,
      };

  /// Живые бумажные сделки (§18, §21, `GET /api/v1/paper/trades`).
  ///
  /// Сопровождение переехало на сервер целиком. На устройстве оно брало
  /// свечи только у биржи напрямую, Bybit отвечает телефону владельца
  /// `403 — CloudFront блокирует доступ из вашей страны`, свечей нет, сверка
  /// не вызывается ни разу — и позиция замирает навсегда: ни тейки, ни стоп,
  /// ни срок не проверяются, а карточка выглядит живой. Так BTCUSDT провисел
  /// несколько дней на «взято тейков: 2 из 3».
  ///
  /// `null` — не «сделок нет», а «спросить не удалось»: пустой список и
  /// молчание движка должны различаться, иначе экран объявит журнал пустым
  /// на ровном месте.
  Future<List<PaperPosition>?> paperTrades({bool liveOnly = true}) async {
    if (!isConfigured) return null;
    try {
      final raw = await _api.getList(
        '$_base/paper/trades?live_only=$liveOnly&limit=100',
      );
      return EngineContract.paperTrades(raw);
    } catch (_) {
      return null;
    }
  }

  /// Состояние загрузки данных: без него пустая выдача неотличима от
  /// «данные не приехали».
  Future<Map<String, dynamic>?> dataStatus() async {
    if (!isConfigured) return null;
    try {
      return await _api.get('$_base/market/status');
    } catch (_) {
      return null;
    }
  }

  Future<Map<String, dynamic>?> health() async {
    if (!isConfigured) return null;
    try {
      return await _api.get('/health');
    } catch (_) {
      return null;
    }
  }

  static String _reason(Object error) {
    final text = error.toString();
    // Причина показывается дословно: «что-то пошло не так» не позволяет
    // отличить обрыв связи от сломанного движка, а это разные действия.
    return text.length > 200 ? '${text.substring(0, 200)}…' : text;
  }
}
