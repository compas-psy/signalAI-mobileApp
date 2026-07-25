import 'package:flutter/widgets.dart';

import 'tokens.dart';

/// Типографика макета: Jost — заголовки, Manrope — текст,
/// JetBrains Mono — все числа (цены, объёмы, R, проценты).
///
/// Шрифты подключены вариативными файлами, поэтому вес задаётся и через
/// [FontWeight] (для переносов/метрик), и через ось `wght` [FontVariation].
abstract final class T {
  static List<FontVariation> _v(int weight) => [FontVariation('wght', weight.toDouble())];

  static FontWeight _w(int weight) => switch (weight) {
        400 => FontWeight.w400,
        500 => FontWeight.w500,
        600 => FontWeight.w600,
        700 => FontWeight.w700,
        800 => FontWeight.w800,
        _ => FontWeight.w400,
      };

  static TextStyle _base(
    String family,
    double size,
    int weight,
    Color color,
    double? letterSpacing,
    double? height,
  ) =>
      TextStyle(
        fontFamily: family,
        fontSize: size,
        fontWeight: _w(weight),
        fontVariations: _v(weight),
        color: color,
        letterSpacing: letterSpacing,
        height: height,
        leadingDistribution: TextLeadingDistribution.even,
      );

  /// Заголовки (Jost).
  static TextStyle jost(
    double size, {
    int weight = 600,
    Color color = C.text,
    double? letterSpacing,
    double? height,
  }) =>
      _base('Jost', size, weight, color, letterSpacing, height);

  /// Основной текст (Manrope).
  static TextStyle body(
    double size, {
    int weight = 400,
    Color color = C.text,
    double? letterSpacing,
    double? height,
  }) =>
      _base('Manrope', size, weight, color, letterSpacing, height);

  /// Числа (JetBrains Mono).
  static TextStyle mono(
    double size, {
    int weight = 400,
    Color color = C.text,
    double? letterSpacing,
    double? height,
  }) =>
      _base('JetBrains Mono', size, weight, color, letterSpacing, height);

  /// Прописной микро-заголовок секции: 11px / 700 / letter-spacing .12em.
  static TextStyle sectionLabel({Color color = C.muted}) =>
      body(11, weight: 700, color: color, letterSpacing: 11 * 0.12);
}
