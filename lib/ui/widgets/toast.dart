import 'package:flutter/widgets.dart';

import '../../state/navigation.dart';
import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'vector_icon.dart';

/// Тост подтверждения действия — появляется сверху, как в макете.
class AppToast extends StatelessWidget {
  const AppToast({
    super.key,
    required this.message,
    this.tone = ToastTone.success,
  });

  final String message;
  final ToastTone tone;

  IconSpec get _icon => switch (tone) {
        ToastTone.success => Icons.check,
        ToastTone.warning => Icons.info,
        ToastTone.failure => Icons.cross,
      };

  Color get _color => switch (tone) {
        ToastTone.success => C.green,
        ToastTone.warning => C.warning,
        ToastTone.failure => C.red,
      };

  Color get _background => switch (tone) {
        ToastTone.success => C.toastBg,
        ToastTone.warning => C.warningFaint,
        ToastTone.failure => C.redFaint,
      };

  Color get _text => switch (tone) {
        // Успех говорит своим цветом, отказ — обычным текстом на цветном
        // фоне: красная надпись на красном читается хуже, а сообщение об
        // отказе обычно длиннее и важнее.
        ToastTone.success => C.toastText,
        _ => C.text,
      };

  @override
  Widget build(BuildContext context) => TweenAnimationBuilder<double>(
        // @keyframes fadeIn: сдвиг на 6px сверху + проявление, 250 мс
        tween: Tween(begin: 0, end: 1),
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
        builder: (context, t, child) => Opacity(
          opacity: t,
          child: Transform.translate(offset: Offset(0, -6 * (1 - t)), child: child),
        ),
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
          decoration: BoxDecoration(
            color: _background,
            border: Border.all(color: _color.withValues(alpha: 0.45)),
            borderRadius: BorderRadius.circular(R.inner),
          ),
          child: Row(
            children: [
              VectorIcon(_icon, size: 15, color: _color),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  message,
                  style: T.body(12, weight: 700, color: _text),
                ),
              ),
            ],
          ),
        ),
      );
}
