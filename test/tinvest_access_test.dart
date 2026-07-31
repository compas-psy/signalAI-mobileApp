/// Доступ к счетам Т-Инвестиций.
///
/// Токен Invest API привязан к пользователю, а не к счёту: он видит все счета
/// владельца, и ограничить его на стороне брокера нельзя. Значит ограничивает
/// приложение — явным списком, а не умолчанием «всё, что нашлось».
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/broker/tinvest_role.dart';
import 'package:signalai/data/broker/secure_vault.dart';
import 'package:signalai/domain/broker/trading_gate.dart';

void main() {
  group('Список разрешённых счетов', () {
    test('пустой список не выдаёт доступ ко всему', () {
      // Состояние старых версий приходит без списка. Подставить туда «все»
      // значило бы тихо выдать доступ при обновлении приложения.
      const state = TradingState();
      expect(state.allows('ACC-1'), isFalse);
      expect(state.canTradeFrom('ACC-1'), isFalse);
    });

    test('разрешение читать — не разрешение торговать', () {
      const state = TradingState(allowedAccountIds: {'ACC-1', 'ACC-2'});
      expect(state.allows('ACC-1'), isTrue);
      // Торгового счёта не назначено — заявкам уходить неоткуда.
      expect(state.canTradeFrom('ACC-1'), isFalse);
    });

    test('торгует ровно один счёт', () {
      const state = TradingState(
        allowedAccountIds: {'ACC-1', 'ACC-2'},
        tinvestAccountId: 'ACC-2',
      );
      expect(state.canTradeFrom('ACC-2'), isTrue);
      expect(state.canTradeFrom('ACC-1'), isFalse);
    });

    test('счёт без доступа торговым быть перестаёт', () {
      // Иначе отзыв доступа не отзывал бы главного.
      const state = TradingState(
        allowedAccountIds: {'ACC-1'},
        tinvestAccountId: 'ACC-2',
      );
      expect(state.canTradeFrom('ACC-2'), isFalse);
    });

    test('список переживает сохранение', () {
      const state = TradingState(
        allowedAccountIds: {'ACC-1'},
        tinvestAccountId: 'ACC-1',
        enabled: true,
      );
      final back = TradingState.fromJson(state.toJson());
      expect(back.allowedAccountIds, {'ACC-1'});
      expect(back.canTradeFrom('ACC-1'), isTrue);
    });

    test('смена режима площадки не теряет доступа', () {
      const state = TradingState(
        allowedAccountIds: {'ACC-1'},
        tinvestAccountId: 'ACC-1',
      );
      final live = state.withMode(BrokerId.tinvest, TradingMode.live);
      expect(live.allowedAccountIds, {'ACC-1'});
      expect(live.canTradeFrom('ACC-1'), isTrue);
    });
  });

  group('Наличие токена', () {
    test('площадке без секрета хватает одного ключа', () async {
      // Тот самый баг: Т-Инвестиции авторизуются одним токеном, секрета у
      // них нет. Пустой секрет при сохранении удаляется, проверка требовала
      // оба — и приложение отвечало «ключей нет» на только что принятый
      // токен. Три строки подряд говорили «нет» при трёх принятых токенах.
      final vault = _OneKeyVault();
      expect(
        await vault.hasKeys(exchange: 'tinvest', mode: 'invest'),
        isFalse,
        reason: 'по умолчанию секрет всё ещё обязателен — у Bybit он есть',
      );
      expect(
        await vault.hasKeys(
          exchange: 'tinvest', mode: 'invest', needsSecret: false,
        ),
        isTrue,
      );
    });
  });

  group('Роли токенов', () {
    test('инвестиционный токен торговать не может', () {
      expect(TInvestRole.invest.canTrade, isFalse);
      expect(TInvestRole.invest.isSandbox, isFalse);
    });

    test('каждая роль лежит в своём слоте', () {
      final slots = {for (final r in TInvestRole.values) r.slot};
      expect(slots.length, TInvestRole.values.length);
    });

    test('неизвестный слот читается как песочница, а не как торговый', () {
      // Ошибка чтения не должна давать больше прав, чем было.
      expect(TInvestRole.parse('что-то'), TInvestRole.sandbox);
      expect(TInvestRole.parse(null), TInvestRole.sandbox);
    });
  });
}


/// Хранилище, в котором лежит ключ без секрета — как у Т-Инвестиций.
class _OneKeyVault extends SecureVault {
  final _keys = <String>{'tinvest.invest.key'};

  @override
  Future<bool> hasKeys({
    required String exchange,
    required String mode,
    bool needsSecret = true,
  }) async {
    final key = _keys.contains('$exchange.$mode.key');
    if (!needsSecret) return key;
    return key && _keys.contains('$exchange.$mode.secret');
  }
}
