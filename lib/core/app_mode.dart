/// Режим установленной сборки.
///
/// `thin` — production sideload: идеи, графики и paper lifecycle только с
/// сервера. `local` остаётся временным legacy/dev-контуром, `demo` — fixture.
abstract final class AppMode {
  static const name =
      String.fromEnvironment('SIGNALAI_MODE', defaultValue: 'local');

  static const thin = name == 'thin';
  static const demo = name == 'demo';
}
