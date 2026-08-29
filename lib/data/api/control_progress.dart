/// Owner-facing progress for a venue-scoped CONTROL vs candidate comparison.
///
/// This is presentation-only math. The comparable count and required sample
/// remain server-owned; the phone merely shows how far the evidence gate is
/// from completion.
class ComparableSampleProgress {
  const ComparableSampleProgress({
    required this.comparable,
    required this.required,
  })  : assert(comparable >= 0),
        assert(required > 0);

  final int comparable;
  final int required;

  bool get adequate => comparable >= required;

  int get remaining => adequate ? 0 : required - comparable;

  String get label => adequate
      ? '$comparable / $required · выборка набрана'
      : '$comparable / $required · осталось $remaining';
}
