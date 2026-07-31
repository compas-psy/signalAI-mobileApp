import 'package:signalai/data/market/bybit_client.dart';
import 'package:signalai/data/market/http_json.dart';
import 'package:signalai/data/market/iss_client.dart';

/// HTTP, который никуда не ходит.
///
/// Существует не для удобства, а потому что тест, случайно уходящий на биржу,
/// — это ловушка с отложенным срабатыванием. Локально `iss.moex.com` был
/// недоступен и отказ приходил за секунду, тесты «Идея на бумаге» проходили и
/// выглядели офлайновыми. На раннере GitHub соединение открывалось и висело,
/// пока не срабатывал тридцатисекундный тайм-аут, — и сборка APK падала на
/// трёх тестах, которые на машине разработчика зелёные.
///
/// Отказ здесь мгновенный и синхронный: репозиторий обязан пережить
/// недоступную биржу, и именно это тест и проверяет.
class OfflineHttp implements HttpJson {
  OfflineHttp([this.message = 'сеть в тестах недоступна']);

  final String message;

  /// Куда пытались сходить. Пустой список — доказательство офлайновости.
  final List<Uri> requested = [];

  @override
  Duration get timeout => const Duration(milliseconds: 1);

  @override
  Future<Map<String, dynamic>> get(Uri uri, {int attempts = 3}) {
    requested.add(uri);
    return Future.error(MarketDataException(message));
  }

  @override
  void close() {}
}

/// Клиент MOEX ISS, который не выходит в сеть.
IssClient offlineIss() => IssClient(http: OfflineHttp('MOEX ISS в тестах отключён'));

/// Клиент Bybit, который не выходит в сеть.
BybitClient offlineBybit() =>
    BybitClient(http: OfflineHttp('Bybit в тестах отключён'));
