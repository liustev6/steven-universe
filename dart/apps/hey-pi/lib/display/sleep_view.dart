import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/app_time.dart';
import '../ui/ambient_background.dart';
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

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = ref.watch(currentMinuteProvider);
    // The calm vignette, not the dashboard background.
    return AmbientBackground(
      child: Center(
        child: Transform.translate(
          offset: sleepClockShiftFor(now),
          child: ClockLockup(now: now, timeSize: 180),
        ),
      ),
    );
  }
}
