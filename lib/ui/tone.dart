import 'package:flutter/widgets.dart';

import '../domain/enums.dart';
import '../theme/tokens.dart';

/// Цвет по семантическому тону значения.
Color toneColor(Tone tone) => switch (tone) {
      Tone.positive => C.green,
      Tone.negative => C.red,
      Tone.accent => C.accent,
      Tone.neutral => C.text,
    };

/// Цвет текста для направления сделки.
Color directionColor(Direction direction) => direction.isLong ? C.green : C.red;

/// Фон бейджа направления.
Color directionBackground(Direction direction) =>
    direction.isLong ? C.greenSoft : C.redSoft;

/// Цвет маркера события по важности (ТЗ §3).
Color impactColor(EventImpact impact) => switch (impact) {
      EventImpact.high => C.red,
      EventImpact.mid => C.accent,
      EventImpact.low => C.dim,
    };
