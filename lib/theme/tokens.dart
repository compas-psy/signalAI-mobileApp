import 'package:flutter/widgets.dart';

/// Дизайн-токены SignalAI.
///
/// Значения взяты один-в-один из макета Claude Design
/// (`design/SignalAI App v2.dc.html`). Палитра построена от логотипа:
/// жёлтый #FFD400 на почти чёрном фоне. Глубина задаётся только цветом
/// поверхностей — теней в макете нет.
abstract final class C {
  // Поверхности
  static const bg = Color(0xFF0B0B0D); // фон экрана
  static const bgPreview = Color(0xFF0E0E10); // фон превью-обвязки макета
  static const card = Color(0xFF141419); // карточка (surface)
  static const inset = Color(0xFF101014); // вложенный блок / фон графика
  static const chip = Color(0xFF1C1C22); // чип, hover-состояние
  static const sheet = Color(0xFF17171C); // модальный шит (surfaceRaised)
  static const segmentActive = Color(0xFF1F1F27); // активный сегмент
  static const navBg = Color(0xFF0D0D10); // таб-бар (rgba(13,13,16,.97))
  static const headerBg = Color(0xFF0B0B0D); // липкий хедер (rgba(11,11,13,.92))

  // Границы
  static const border = Color(0xFF24242B);
  static const divider = Color(0xFF1C1C23);
  static const dividerSoft = Color(0xFF1B1B20);
  static const borderStrong = Color(0xFF2C2C34);
  static const borderHover = Color(0xFF3A3A42);
  static const handle = Color(0xFF33333B);
  static const toggleOff = Color(0xFF2A2A31);

  // Текст
  static const text = Color(0xFFF2F2EF);
  static const textSoft = Color(0xFFE5E5EA);
  static const textSecondary = Color(0xFFA9A9B2); // пояснения под заголовком
  static const muted = Color(0xFF9696A1);
  static const faint = Color(0xFF666670); // микро-подписи ВХОД/СТОП/ЦЕЛИ
  static const dim = Color(0xFF5A5A64);
  static const navInactive = faint;
  static const axis = Color(0xFF4E4E58); // подписи шкалы графика
  static const grid = Color(0xFF1B1B22); // сетка графика

  // Акценты.
  //
  // Семантика строгая (ТЗ v3 §2): жёлтый — только действие и фокус, зелёный и
  // красный — только финансовый результат и направление, синий — учётный
  // статус и ядро, фиолетовый — тактика и переводы, оранжевый — сверка и
  // неполные данные. В версии 2 жёлтый значил и «важно», и «нажми», и
  // «хорошо» — от этого акцент перестал работать как акцент.
  static const accent = Color(0xFFFFD400);
  static const accentHover = Color(0xFFFFE045);
  static const green = Color(0xFF2FD575);
  static const red = Color(0xFFFF5C5C);
  static const info = Color(0xFF63A5FF);
  static const tactical = Color(0xFFB38CFF);
  static const warning = Color(0xFFFFB74D);

  // Полупрозрачные заливки новых акцентов.
  static const infoFaint = Color(0x1F63A5FF);
  static const infoBorder = Color(0x4D63A5FF);
  static const tacticalFaint = Color(0x1FB38CFF);
  static const tacticalBorder = Color(0x4DB38CFF);
  static const warningFaint = Color(0x1FFFB74D);
  static const warningBorder = Color(0x4DFFB74D);
  static const redFaint = Color(0x1FFF5C5C);
  static const redBorder = Color(0x4DFF5C5C);

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
  static const card = 18.0;
  static const inner = 13.0;
  static const inset = 11.0;
  static const chip = 7.0;
  static const chipLg = 9.0;
  static const button = 14.0;
  static const sheet = 22.0;
  static const pill = 99.0;
}

/// Отступы макета: экранные поля 18, внутри карточек 13–15, gap 10–12.
abstract final class S {
  static const screen = 18.0;
  static const cardPad = 14.0;
  static const gap = 10.0;
}
