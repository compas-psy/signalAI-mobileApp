/// Что песочница подтвердила на самом деле.
///
/// Бумажный журнал и песочница отвечают на разные вопросы. Журнал считает
/// результат сам, вперёд по свечам: он про то, зарабатывает ли стратегия.
/// Песочница — настоящий API брокера с ненастоящими деньгами: она про то,
/// примет ли он заявку, верно ли посчитан лот, встанет ли защита. На живой
/// счёт нужны оба ответа, и раньше второго не было вовсе: в настройках стояла
/// захардкоженная строка «защитный стоп ещё не проверен на песочнице»,
/// которая показывалась всегда и снять её было нечем.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:signalai/data/local_analysis_repository.dart';
import 'package:signalai/data/local_store.dart';
import 'package:signalai/domain/broker/broker.dart';
import 'package:signalai/domain/broker/sandbox_proof.dart';
import 'package:signalai/domain/broker/trading_gate.dart';
import 'package:signalai/domain/ledger/signal_ledger.dart';

import 'support/offline_http.dart';
import 'trading_desk_test.dart'
    show FakeVault, provenLedger, seedDigest, signal;

void main() {
  group('Отпечаток песочницы', () {
    test('пустой отпечаток называет, чего не хватает', () {
      const proof = SandboxProof();
      expect(proof.complete, isFalse);
      expect(proof.missing, contains('ни заявки'));
    });

    test('принятая заявка без стопа не считается проверкой', () {
      // Позиция без стопа — единственный способ потерять больше
      // запланированного. «Заявку приняли» этого не закрывает.
      final proof = const SandboxProof().withOrder();
      expect(proof.complete, isFalse);
      expect(proof.missing, contains('стоп'));
    });

    test('заявка и стоп вместе закрывают вопрос', () {
      final proof = const SandboxProof().withOrder().withStop();
      expect(proof.complete, isTrue);
      expect(proof.missing, isEmpty);
    });

    test('отпечаток переживает сохранение', () {
      final proof = const SandboxProof()
          .withOrder(at: DateTime(2025, 5, 1), note: 'принято')
          .withStop(at: DateTime(2025, 5, 1));
      final back = SandboxProof.fromJson(proof.toJson());
      expect(back.complete, isTrue);
      expect(back.note, 'принято');
      expect(back.lastAt, DateTime(2025, 5, 1));
    });
  });

  group('Допуск и механика', () {
    test('доказанная стратегия без песочницы к живым деньгам не пускается', () {
      final verdict = LiveTradingGate()
          .evaluate(provenLedger(), proof: const SandboxProof());
      expect(verdict.allowed, isFalse);
      expect(verdict.reason, contains('песочница'));
      // Прогресс полный: по стратегии владельцу делать больше нечего,
      // и показывать ему незаполненную шкалу было бы неправдой.
      expect(verdict.progress, 1);
    });

    test('проверенная механика снимает запрет', () {
      final verdict = LiveTradingGate().evaluate(
        provenLedger(),
        proof: const SandboxProof().withOrder().withStop(),
      );
      expect(verdict.allowed, isTrue);
    });

    test('недоказанная стратегия не получает вторую причину отказа', () {
      // Иначе владельцу пришлось бы разбираться в двух отказах вместо
      // одного, хотя чинить всё равно надо сначала первый.
      final verdict =
          LiveTradingGate().evaluate(SignalLedger(), proof: const SandboxProof());
      expect(verdict.allowed, isFalse);
      expect(verdict.reason, contains('бумажных сделок'));
      expect(verdict.reason, isNot(contains('песочниц')));
    });
  });

  group('Площадки без песочницы', () {
    test('песочница есть только у Т-Инвестиций', () {
      // У Bybit тестнет как сервис существует, но доступа к нему у владельца
      // нет: крипта либо виртуальная на устройстве, либо живая.
      expect(BrokerId.tinvest.hasSandbox, isTrue);
      expect(BrokerId.bybit.hasSandbox, isFalse);
    });

    test('Bybit не блокируется требованием, которое ему нечем выполнить',
        () async {
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: LocalStore.inMemory(),
        vault: FakeVault(),
      );
      final trading = (await repository.fetchSettings()).trading!;
      final bybit = trading.brokers.firstWhere((b) => b.id == 'bybit');
      final tinvest = trading.brokers.firstWhere((b) => b.id == 'tinvest');

      expect(bybit.liveAllowed, isTrue);
      expect(bybit.liveBlockedReason, isEmpty);
      // А вот у Т-Инвестиций причина есть — и она измеренная, а не
      // захардкоженная: прогона не было.
      expect(tinvest.liveAllowed, isFalse);
      expect(tinvest.liveBlockedReason, contains('песочница'));
    });
  });

  group('Бумажная сделка и песочница', () {
    test('без токена песочницы идея ведётся и ничего лишнего не обещает',
        () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: store,
        vault: FakeVault(),
      );

      final answer = await repository.trackOnPaper('sig-1');
      expect(answer, contains('заведена'));
      expect(answer, isNot(contains('песочниц')));
      expect(repository.ledger.trades, hasLength(1));
      // Отпечаток не появился: обещать проверку, которой не было, — ровно та
      // ошибка, ради которой всё это и делалось.
      expect(repository.sandboxProofOf(BrokerId.tinvest).complete, isFalse);
    });

    test('аварийная остановка означает «никуда и ничего»', () async {
      final store = LocalStore.inMemory();
      await seedDigest(store, [signal()]);
      final vault = FakeVault();
      await vault.saveKeys(
        exchange: 'tinvest',
        mode: 'sandbox',
        apiKey: 'TOKEN',
        apiSecret: '',
      );
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: store,
        vault: vault,
      );
      await repository.setKillSwitch(true);

      // Журнал ведётся: остановка запрещает заявки, а не наблюдение.
      final answer = await repository.trackOnPaper('sig-1');
      expect(answer, contains('заведена'));
      expect(answer, isNot(contains('песочниц')));
      expect(repository.sandboxProofOf(BrokerId.tinvest).complete, isFalse);
    });

    test('отпечаток читается с диска при следующем запуске', () async {
      final store = LocalStore.inMemory();
      await store.write('state', {
        'sandbox_proof': {
          'tinvest': const SandboxProof().withOrder().withStop().toJson(),
        },
      });
      final repository = LocalAnalysisRepository(
        iss: offlineIss(),
        bybit: offlineBybit(),
        store: store,
        vault: FakeVault(),
      );

      final trading = (await repository.fetchSettings()).trading!;
      final tinvest = trading.brokers.firstWhere((b) => b.id == 'tinvest');
      expect(repository.sandboxProofOf(BrokerId.tinvest).complete, isTrue);
      expect(tinvest.liveAllowed, isTrue);
      expect(tinvest.liveBlockedReason, isEmpty);
    });
  });
}
