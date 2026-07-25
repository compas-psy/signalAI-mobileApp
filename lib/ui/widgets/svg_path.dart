import 'dart:math' as math;
import 'dart:ui';

/// Разбор атрибута `d` из SVG в [Path].
///
/// Нужен, чтобы иконки из макета переносились дословно, а не перерисовывались
/// на глаз. Поддерживаются все команды пути: M/L/H/V/C/S/Q/T/A/Z в абсолютной
/// и относительной форме.
Path parseSvgPath(String d) {
  final parser = _PathParser(d);
  return parser.parse();
}

class _PathParser {
  _PathParser(this._source);

  final String _source;
  int _i = 0;

  final Path _path = Path();
  Offset _current = Offset.zero;
  Offset _start = Offset.zero;
  Offset? _lastCubicControl;
  Offset? _lastQuadControl;

  Path parse() {
    var command = '';
    while (true) {
      _skipSeparators();
      if (_i >= _source.length) break;

      final ch = _source[_i];
      if (_isCommand(ch)) {
        command = ch;
        _i++;
      } else if (command.isEmpty) {
        throw FormatException('SVG path начинается не с команды: $_source');
      } else if (command == 'M') {
        command = 'L'; // повтор аргументов moveto трактуется как lineto
      } else if (command == 'm') {
        command = 'l';
      }

      _runCommand(command);
    }
    return _path;
  }

  void _runCommand(String command) {
    final relative = command.toLowerCase() == command;
    switch (command.toLowerCase()) {
      case 'm':
        final p = _point(relative);
        _path.moveTo(p.dx, p.dy);
        _current = p;
        _start = p;
        _resetControls();
      case 'l':
        final p = _point(relative);
        _path.lineTo(p.dx, p.dy);
        _current = p;
        _resetControls();
      case 'h':
        final x = _number() + (relative ? _current.dx : 0);
        _path.lineTo(x, _current.dy);
        _current = Offset(x, _current.dy);
        _resetControls();
      case 'v':
        final y = _number() + (relative ? _current.dy : 0);
        _path.lineTo(_current.dx, y);
        _current = Offset(_current.dx, y);
        _resetControls();
      case 'c':
        final c1 = _point(relative);
        final c2 = _point(relative);
        final end = _point(relative);
        _path.cubicTo(c1.dx, c1.dy, c2.dx, c2.dy, end.dx, end.dy);
        _current = end;
        _lastCubicControl = c2;
        _lastQuadControl = null;
      case 's':
        final c1 = _reflect(_lastCubicControl);
        final c2 = _point(relative);
        final end = _point(relative);
        _path.cubicTo(c1.dx, c1.dy, c2.dx, c2.dy, end.dx, end.dy);
        _current = end;
        _lastCubicControl = c2;
        _lastQuadControl = null;
      case 'q':
        final c = _point(relative);
        final end = _point(relative);
        _path.quadraticBezierTo(c.dx, c.dy, end.dx, end.dy);
        _current = end;
        _lastQuadControl = c;
        _lastCubicControl = null;
      case 't':
        final c = _reflect(_lastQuadControl);
        final end = _point(relative);
        _path.quadraticBezierTo(c.dx, c.dy, end.dx, end.dy);
        _current = end;
        _lastQuadControl = c;
        _lastCubicControl = null;
      case 'a':
        final rx = _number();
        final ry = _number();
        final rotation = _number();
        final largeArc = _flag();
        final sweep = _flag();
        final end = _point(relative);
        if (rx == 0 || ry == 0) {
          _path.lineTo(end.dx, end.dy);
        } else {
          _path.arcToPoint(
            end,
            radius: Radius.elliptical(rx.abs(), ry.abs()),
            rotation: rotation,
            largeArc: largeArc,
            // В SVG sweep=1 — по часовой стрелке, как и clockwise во Flutter.
            clockwise: sweep,
          );
        }
        _current = end;
        _resetControls();
      case 'z':
        _path.close();
        _current = _start;
        _resetControls();
      default:
        throw FormatException('Неизвестная команда SVG path: $command');
    }
  }

  void _resetControls() {
    _lastCubicControl = null;
    _lastQuadControl = null;
  }

  /// Отражение предыдущей контрольной точки относительно текущей (S и T).
  Offset _reflect(Offset? control) =>
      control == null ? _current : _current * 2 - control;

  Offset _point(bool relative) {
    final x = _number();
    final y = _number();
    return relative ? Offset(_current.dx + x, _current.dy + y) : Offset(x, y);
  }

  /// Флаг дуги — ровно один символ 0/1, даже если он слипся со следующим числом.
  bool _flag() {
    _skipSeparators();
    final ch = _source[_i];
    if (ch != '0' && ch != '1') {
      throw FormatException('Ожидался флаг дуги 0/1, получено "$ch" в $_source');
    }
    _i++;
    return ch == '1';
  }

  double _number() {
    _skipSeparators();
    final start = _i;
    if (_i < _source.length && (_source[_i] == '-' || _source[_i] == '+')) _i++;
    while (_i < _source.length && _isDigit(_source[_i])) {
      _i++;
    }
    if (_i < _source.length && _source[_i] == '.') {
      _i++;
      while (_i < _source.length && _isDigit(_source[_i])) {
        _i++;
      }
    }
    if (_i < _source.length && (_source[_i] == 'e' || _source[_i] == 'E')) {
      _i++;
      if (_i < _source.length && (_source[_i] == '-' || _source[_i] == '+')) _i++;
      while (_i < _source.length && _isDigit(_source[_i])) {
        _i++;
      }
    }
    if (start == _i) {
      throw FormatException('Ожидалось число на позиции $_i: $_source');
    }
    return double.parse(_source.substring(start, _i));
  }

  void _skipSeparators() {
    while (_i < _source.length) {
      final ch = _source[_i];
      if (ch == ' ' || ch == ',' || ch == '\n' || ch == '\t' || ch == '\r') {
        _i++;
      } else {
        break;
      }
    }
  }

  static bool _isDigit(String ch) {
    final code = ch.codeUnitAt(0);
    return code >= 0x30 && code <= 0x39;
  }

  static bool _isCommand(String ch) => 'MmLlHhVvCcSsQqTtAaZz'.contains(ch);
}

/// Прямоугольник, в который вписан путь, с учётом толщины обводки.
Rect strokeBounds(Path path, double strokeWidth) =>
    path.getBounds().inflate(strokeWidth / 2);

/// Угол в радианах из градусов — для дуг конфлюэнс-кольца.
double degToRad(double deg) => deg * math.pi / 180;
