/// Как приложение работает, пока оно закрыто.
enum BackgroundMode {
  /// Legacy-имя основного режима. Теперь это один bounded server poll,
  /// запланированный примерно раз в 15 минут; долгоживущего сервиса нет.
  persistent,

  /// Legacy-имя альтернативного режима. Сохранено для чтения старых настроек,
  /// но выполняет тот же bounded server poll примерно раз в 15 минут.
  burst;

  static BackgroundMode parse(String? v) => BackgroundMode.values.firstWhere(
        (m) => m.name == v,
        orElse: () => BackgroundMode.persistent,
      );

  String get label => 'опрос сервера примерно раз в 15 минут';

  BackgroundMode get other => this == BackgroundMode.persistent
      ? BackgroundMode.burst
      : BackgroundMode.persistent;
}
