String formatCapitalAmount(double value) {
  final magnitude = value.abs();
  if (magnitude > 0 && magnitude < 0.01) return value.toStringAsFixed(6);
  if (magnitude >= 1000) return value.toStringAsFixed(0);
  return value.toStringAsFixed(2);
}
