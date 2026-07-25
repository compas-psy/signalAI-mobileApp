import 'package:flutter/widgets.dart';

import '../../theme/tokens.dart';
import '../../theme/typography.dart';
import 'vector_icon.dart';

/// Тост подтверждения действия — появляется сверху, как в макете.
class AppToast extends StatelessWidget {
  const AppToast({super.key, required this.message});

  final String message;

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
            color: C.toastBg,
            border: Border.all(color: const Color(0x732FD575)),
            borderRadius: BorderRadius.circular(R.inner),
          ),
          child: Row(
            children: [
              const VectorIcon(Icons.check, size: 15, color: C.green),
              const SizedBox(width: 9),
              Expanded(
                child: Text(
                  message,
                  style: T.body(12, weight: 700, color: C.toastText),
                ),
              ),
            ],
          ),
        ),
      );
}
