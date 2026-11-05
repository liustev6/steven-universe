import 'dart:math' as math;

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
    final now = ref.watch(currentMinuteProvider);
    // A black layer painted over everything dims the page evenly.
    return DecoratedBox(
      position: DecorationPosition.foreground,
      decoration: const BoxDecoration(color: Color.fromRGBO(0, 0, 0, dimming)),
      child: DecoratedBox(
        decoration: const BoxDecoration(
          gradient: RadialGradient(
            center: Alignment(0, -0.05),
            radius: 1.1,
            colors: [PanelColors.sleepGlow, PanelColors.sleepBackground],
          ),
        ),
        child: Center(
          child: Transform.translate(
            offset: sleepClockShiftFor(now),
            child: SleepClock(now: now),
          ),
        ),
      ),
    );
  }
}

/// Big time with AM/PM stacked over the seconds on its right, date below.
class SleepClock extends StatelessWidget {
  const SleepClock({super.key, required this.now});

  final DateTime now;

  /// Font size of the main time; every other size derives from it.
  static const double timeSize = 180;

  /// Height of digits as a fraction of font size (Inter: 0.727).
  static const double _capHeight = 0.727;

  /// Distance from the top of the time row to the shared text baseline.
  /// Equal to the digit height, so the row starts exactly at the top of the
  /// digits and [Center] centres what you see, not empty space above it.
  static const double _baseline = timeSize * _capHeight;

  static const _figures = [FontFeature.tabularFigures()];

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Baseline(
              baseline: _baseline,
              baselineType: TextBaseline.alphabetic,
              child: _GradientText(
                ClockFormats.time.format(now),
                style: const TextStyle(
                  fontSize: timeSize,
                  fontWeight: FontWeight.w600,
                  height: 1,
                  letterSpacing: -10,
                  fontFeatures: _figures,
                ),
              ),
            ),
            const SizedBox(width: timeSize * 0.1),
            // Both children start at the top and are centred horizontally on
            // each other, whichever is wider; Baseline sets their heights.
            Stack(
              alignment: Alignment.topCenter,
              children: [
                const Baseline(
                  baseline: _baseline,
                  baselineType: TextBaseline.alphabetic,
                  child: _Seconds(
                    style: TextStyle(
                      fontSize: timeSize * 0.4,
                      fontWeight: FontWeight.w500,
                      height: 1,
                      color: PanelColors.sleepSecondary,
                      fontFeatures: _figures,
                    ),
                  ),
                ),
                Baseline(
                  baseline: _baseline - timeSize * 0.41,
                  baselineType: TextBaseline.alphabetic,
                  child: Text(
                    ClockFormats.period.format(now),
                    style: const TextStyle(
                      fontSize: timeSize * 0.3,
                      fontWeight: FontWeight.w500,
                      height: 1,
                      letterSpacing: 1,
                      color: PanelColors.sleepSecondary,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
        const SizedBox(height: timeSize * 0.1),
        Text(
          ClockFormats.date.format(now),
          style: const TextStyle(
            fontSize: timeSize * 0.18,
            fontWeight: FontWeight.w400,
            color: PanelColors.sleepSecondary,
          ),
        ),
      ],
    );
  }
}

/// Seconds digits. The only part of the sleep view that changes every
/// second, so it watches the per-second time and repaints in isolation.
class _Seconds extends ConsumerWidget {
  const _Seconds({required this.style});

  final TextStyle style;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final second = ref.watch(appTimeProvider.select((t) => t.second));
    return RepaintBoundary(
      child: Text(second.toString().padLeft(2, '0'), style: style),
    );
  }
}

/// Text filled with a soft top-to-bottom white→grey gradient.
class _GradientText extends StatelessWidget {
  const _GradientText(this.text, {required this.style});

  final String text;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    return ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: (bounds) => const LinearGradient(
        begin: Alignment.topCenter,
        end: Alignment.bottomCenter,
        colors: [PanelColors.sleepTimeTop, PanelColors.sleepTimeBottom],
      ).createShader(bounds),
      // Negative letter spacing makes the first and last digits overhang the
      // text box (half on each side); pad it back so the box, and the
      // gradient, cover every glyph.
      child: Padding(
        padding: EdgeInsets.symmetric(
          horizontal: math.max(0, -(style.letterSpacing ?? 0)) / 2,
        ),
        child: Text(text, style: style.copyWith(color: Colors.white)),
      ),
    );
  }
}
