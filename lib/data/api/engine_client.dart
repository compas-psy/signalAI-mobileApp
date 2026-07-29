import '../../domain/idea/idea.dart';
import 'api_client.dart';
import 'api_config.dart';
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

  bool get isConfigured => ApiConfig.isConfigured;

  /// Карточки дня (§16): не больше трёх, с причиной, если торговать нечего.
  Future<EngineIdeas> today() async {
    if (!isConfigured) {
      return const EngineIdeas.unavailable(
        'Адрес движка не задан в сборке. Идеи считает сервер — '
        'без него показывать нечего.',
      );
    }
    try {
      final json = await _api.get('$_base/ideas/today');
      final cards = (json['cards'] as List<dynamic>? ?? const []);
      final ideas = <Idea>[];
      for (final card in cards) {
        if (card is Map<String, dynamic>) {
          ideas.add(EngineContract.idea(card));
        }
      }
      return EngineIdeas(
        ideas: ideas,
        noSetupsReason: ideas.isEmpty
            ? (json['reason'] as String? ??
                'Движок отработал и не нашёл сетапов, проходящих допуск §16.')
            : null,
      );
    } catch (error) {
      return EngineIdeas.unavailable(_reason(error));
    }
  }

  /// Полная карточка с планом, разбором оценки, доказательствами и разметкой.
  Future<Idea?> detail(String id) async {
    if (!isConfigured) return null;
    final json = await _api.get('$_base/ideas/$id');
    return EngineContract.idea(json);
  }

  /// Лента идей, включая непоказанные (§12): журнал обязан отвечать на
  /// вопрос, что система нашла, но не показала.
  Future<EngineIdeas> feed({int limit = 50}) async {
    if (!isConfigured) {
      return const EngineIdeas.unavailable('Адрес движка не задан в сборке.');
    }
    try {
      // Лента отдаётся массивом верхнего уровня — оборачиваем его на
      // стороне клиента, чтобы разбор был один и тот же для обеих форм.
      final json = await _api.getList('$_base/ideas?limit=$limit');
      final ideas = <Idea>[];
      for (final item in json) {
        if (item is Map<String, dynamic>) ideas.add(EngineContract.idea(item));
      }
      return EngineIdeas(ideas: ideas);
    } catch (error) {
      return EngineIdeas.unavailable(_reason(error));
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
