import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/app_time.dart';
import '../ui/theme.dart';
import '../widgets/clock_widget.dart';

/// Offsets the sleep clock cycles through, one step every 3 minutes, so the
/// same pixels aren't lit for hours (LCD image-retention mitigation).
const sleepClockShifts = [
  Offset(0, 0),
  Offset(8, 5),
  Offset(-6, 8),
  Offset(5, -8),
  Offset(-8, -4),
];

Offset sleepClockShiftFor(DateTime time) {
  final step =
      time.millisecondsSinceEpoch ~/ Duration.millisecondsPerMinute ~/ 3;
  return sleepClockShifts[step % sleepClockShifts.length];
}

/// Ambient clock shown in `sleep`. No animations. Everything rebuilds once a
/// minute except the seconds digits, which redraw in their own small layer.
class SleepView extends ConsumerWidget {
  const SleepView({super.key});

  /// How much darker the whole page is drawn (0 = as designed, 1 = black).
  static const double dimming = 0.15;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final palette = context.palette;
    final now = ref.watch(currentMinuteProvider);
    // A black layer painted over everything dims the page evenly.
    return DecoratedBox(
      position: DecorationPosition.foreground,
      decoration: const BoxDecoration(color: Color.fromRGBO(0, 0, 0, dimming)),
      child: DecoratedBox(
        // The sleep view's own calm vignette, not the dashboard background.
        decoration: BoxDecoration(
          gradient: RadialGradient(
            center: const Alignment(0, -0.05),
            radius: 1.1,
            colors: [palette.sleepGlow, palette.sleepBackground],
          ),
        ),
        child: Center(
          child: Transform.translate(
            offset: sleepClockShiftFor(now),
            child: ClockLockup(now: now, timeSize: 180),
          ),
        ),
      ),
    );
  }
}
