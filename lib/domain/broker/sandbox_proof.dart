/// Что песочница подтвердила на самом деле.
///
/// Бумажный журнал и песочница отвечают на **разные** вопросы, и путать их
/// нельзя.
///
/// Бумажная сделка живёт на устройстве: сигнал проживается вперёд по реальным
/// свечам с издержками и проскальзыванием. Она отвечает, зарабатывает ли
/// стратегия. Брокера в этом нет вовсе.
///
/// Песочница — настоящий API брокера с ненастоящими деньгами. Она отвечает на
/// другое: примет ли брокер заявку, верно ли посчитан лот, встанет ли на своё
/// место защитный стоп, сработает ли связка «вход плюс защита». Это механика
/// исполнения, и на бумаге она не проверяется никак.
///
/// Живые деньги требуют обоих ответов. Стратегия, прибыльная на бумаге, но с
/// неверным шагом лота, потеряет на первой же заявке. Поэтому допуск смотрит
/// и на журнал, и на этот отпечаток.
///
/// До появления этого файла в интерфейсе стояла строка «защитный стоп ещё не
/// проверен на песочнице» — захардкоженная. Она показывалась всегда,
/// независимо от того, был ли прогон: приложение заявляло проверку, которой
/// не делало.
library;

class SandboxProof {
  const SandboxProof({
    this.orderAccepted = false,
    this.stopPlaced = false,
    this.lastAt,
    this.note = '',
  });

  /// Брокер принял заявку на вход.
  final bool orderAccepted;

  /// Брокер принял защитную заявку.
  ///
  /// Отдельно от входа намеренно: позиция без стопа — единственный способ
  /// потерять больше запланированного, и именно эта часть механики обязана
  /// быть проверена до живых денег.
  final bool stopPlaced;

  /// Когда песочница подтвердила это в последний раз.
  final DateTime? lastAt;

  /// Что именно ответил брокер — для экрана настроек.
  final String note;

  /// Механика исполнения проверена полностью.
  bool get complete => orderAccepted && stopPlaced;

  /// Чего ещё не хватает. Пустая строка — всё проверено.
  String get missing {
    if (complete) return '';
    if (!orderAccepted && !stopPlaced) {
      return 'песочница ещё не принимала ни заявки, ни защитного стопа';
    }
    if (!orderAccepted) return 'заявка на вход в песочнице не проверена';
    return 'защитный стоп в песочнице не проверен';
  }

  SandboxProof withOrder({DateTime? at, String note = ''}) => SandboxProof(
        orderAccepted: true,
        stopPlaced: stopPlaced,
        lastAt: at ?? DateTime.now(),
        note: note.isEmpty ? this.note : note,
      );

  SandboxProof withStop({DateTime? at, String note = ''}) => SandboxProof(
        orderAccepted: orderAccepted,
        stopPlaced: true,
        lastAt: at ?? DateTime.now(),
        note: note.isEmpty ? this.note : note,
      );

  Map<String, dynamic> toJson() => {
        'order_accepted': orderAccepted,
        'stop_placed': stopPlaced,
        if (lastAt != null) 'last_at': lastAt!.toIso8601String(),
        'note': note,
      };

  factory SandboxProof.fromJson(Map<String, dynamic> j) => SandboxProof(
        orderAccepted: j['order_accepted'] as bool? ?? false,
        stopPlaced: j['stop_placed'] as bool? ?? false,
        lastAt: DateTime.tryParse(j['last_at'] as String? ?? ''),
        note: j['note'] as String? ?? '',
      );
}
