import 'package:flutter/widgets.dart';

/// Дизайн-токены SignalAI.
///
/// Значения взяты один-в-один из макета Claude Design
/// (`design/SignalAI App.dc.html`). Палитра построена от логотипа:
/// жёлтый #FFD400 на почти чёрном фоне.
abstract final class C {
  // Поверхности
  static const bg = Color(0xFF0B0B0D); // фон экрана
  static const bgPreview = Color(0xFF0E0E10); // фон превью-обвязки макета
  static const card = Color(0xFF16161A); // карточка
  static const inset = Color(0xFF101014); // вложенный блок / фон графика
  static const chip = Color(0xFF1C1C22); // чип, hover-состояние
  static const sheet = Color(0xFF17171C); // модальный шит
  static const navBg = Color(0xFF0D0D10); // таб-бар (rgba(13,13,16,.97))
  static const headerBg = Color(0xFF0B0B0D); // липкий хедер (rgba(11,11,13,.92))

  // Границы
  static const border = Color(0xFF26262C);
  static const divider = Color(0xFF1D1D24);
  static const dividerSoft = Color(0xFF1B1B20);
  static const borderStrong = Color(0xFF2C2C34);
  static const borderHover = Color(0xFF3A3A42);
  static const handle = Color(0xFF33333B);
  static const toggleOff = Color(0xFF2A2A31);

  // Текст
  static const text = Color(0xFFF2F2EF);
  static const textSoft = Color(0xFFE5E5EA);
  static const textSecondary = Color(0xFFC9C9D1);
  static const muted = Color(0xFF8E8E98);
  static const dim = Color(0xFF5A5A64);
  static const navInactive = Color(0xFF6B6B75);
  static const axis = Color(0xFF4E4E58); // подписи шкалы графика
  static const grid = Color(0xFF1B1B22); // сетка графика

  // Акценты
  static const accent = Color(0xFFFFD400);
  static const accentHover = Color(0xFFFFE045);
  static const green = Color(0xFF2FD575);
  static const red = Color(0xFFFF5C5C);

  // Чипы ценовой шкалы на графике
  static const chipSl = Color(0xFFE5484D);
  static const chipTp = Color(0xFF1E7A46);
  static const chipTpText = Color(0xFFD7FFE8);
  static const onAccent = Color(0xFF111111);

  // Тост
  static const toastBg = Color(0xFF123322);
  static const toastText = Color(0xFF7BE6A8);

  // Прозрачные заливки макета
  static const greenSoft = Color(0x212FD575); // rgba(47,213,117,.13)
  static const redSoft = Color(0x21FF5C5C); // rgba(255,92,92,.13)
  static const greenFaint = Color(0x1A2FD575); // rgba(47,213,117,.10)
  static const greenBorder = Color(0x402FD575); // rgba(47,213,117,.25)
  static const accentFaint = Color(0x14FFD400); // rgba(255,212,0,.08)
  static const accentBorder = Color(0x40FFD400); // rgba(255,212,0,.25)
}

/// Радиусы скруглений из макета.
abstract final class R {
  static const card = 16.0;
  static const inner = 12.0;
  static const inset = 10.0;
  static const chip = 6.0;
  static const chipLg = 8.0;
  static const button = 14.0;
  static const sheet = 22.0;
  static const pill = 99.0;
}
