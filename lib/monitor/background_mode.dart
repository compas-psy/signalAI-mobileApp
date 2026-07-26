/// Как приложение работает, пока оно закрыто.
enum BackgroundMode {
  /// Сервис живёт постоянно и спит между часовыми прогонами.
  ///
  /// Реакция быстрее, но в шторке всегда висит служебная строка, а на
  /// Android 15+ система снимает такой сервис после шести часов работы в
  /// сутки — дальше контур продолжает часовыми пробуждениями от будильника.
  persistent,

  /// Будильник поднимает приложение раз в час, оно делает один прогон и
  /// засыпает. Служебная строка видна секунды, батарея почти не тратится.
  burst;

  static BackgroundMode parse(String? v) => BackgroundMode.values.firstWhere(
        (m) => m.name == v,
        orElse: () => BackgroundMode.persistent,
      );

  String get label => switch (this) {
        BackgroundMode.persistent => 'постоянно следит',
        BackgroundMode.burst => 'раз в час',
      };

  BackgroundMode get other =>
      this == BackgroundMode.persistent ? BackgroundMode.burst : BackgroundMode.persistent;
}
