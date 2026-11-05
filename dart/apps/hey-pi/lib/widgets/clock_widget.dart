import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../layout/panel_widget.dart';
import '../state/app_time.dart';
import '../ui/glass.dart';
import '../ui/theme.dart';

/// Date/time formats shared by the dashboard clock and the sleep clock.
abstract final class ClockFormats {
  static final time = DateFormat('h:mm');
  static final period = DateFormat('a');
  static final date = DateFormat('EEEE, MMMM d');
}

/// The clock design shared by the sleep view and the dashboard card: big
/// semi-bold time in the palette's primary (shaded) colour, AM/PM stacked over the seconds
/// on its right, and the date below. Every size derives from [timeSize].
class ClockLockup extends StatelessWidget {
  const ClockLockup({
    super.key,
    required this.now,
    required this.timeSize,
    this.alignment = CrossAxisAlignment.center,
  });

  final DateTime now;

  /// Font size of the main time.
  final double timeSize;

  /// Horizontal alignment of the time row and the date.
  final CrossAxisAlignment alignment;

  /// Height of digits as a fraction of font size (Inter: 0.727).
  static const double _capHeight = 0.727;

  static const _figures = [FontFeature.tabularFigures()];

  /// Distance from the top of the time row to the shared text baseline.
  /// Equal to the digit height, so the row starts exactly at the top of the
  /// digits and alignment uses what you see, not empty space above it.
  double get _baseline => timeSize * _capHeight;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: alignment,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Baseline(
              baseline: _baseline,
              baselineType: TextBaseline.alphabetic,
              child: _ShadedTime(
                ClockFormats.time.format(now),
                style: TextStyle(
                  fontSize: timeSize,
                  fontWeight: FontWeight.w600,
                  height: 1,
                  letterSpacing: -timeSize * 0.055,
                  fontFeatures: _figures,
                ),
              ),
            ),
            SizedBox(width: timeSize * 0.1),
            // Both children start at the top and are centred horizontally on
            // each other, whichever is wider; Baseline sets their heights.
            Stack(
              alignment: Alignment.topCenter,
              children: [
                Baseline(
                  baseline: _baseline,
                  baselineType: TextBaseline.alphabetic,
                  child: _Seconds(
                    style: TextStyle(
                      fontSize: timeSize * 0.4,
                      fontWeight: FontWeight.w500,
                      height: 1,
                      color: palette.secondary,
                      fontFeatures: _figures,
                    ),
                  ),
                ),
                Baseline(
                  baseline: _baseline - timeSize * 0.41,
                  baselineType: TextBaseline.alphabetic,
                  child: Text(
                    ClockFormats.period.format(now),
                    style: TextStyle(
                      fontSize: timeSize * 0.3,
                      fontWeight: FontWeight.w500,
                      height: 1,
                      letterSpacing: 1,
                      color: palette.secondary,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ),
        SizedBox(height: timeSize * 0.1),
        Text(
          ClockFormats.date.format(now),
          style: TextStyle(
            fontSize: timeSize * 0.18,
            fontWeight: FontWeight.w400,
            color: palette.secondary,
          ),
        ),
      ],
    );
  }
}

/// Dashboard clock card: the [ClockLockup], aligned to the start (left) and
/// scaled down if it doesn't fit the card.
class ClockWidget extends PanelWidget {
  const ClockWidget({super.key});

  @override
  int get columns => 4;

  @override
  int get rows => 2;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = ref.watch(currentMinuteProvider);
    return GlassPanel(
      child: Align(
        alignment: Alignment.centerLeft,
        child: FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: ClockLockup(
            now: now,
            timeSize: 80,
            alignment: CrossAxisAlignment.start,
          ),
        ),
      ),
    );
  }
}

/// Seconds digits. The only part of a clock that changes every second, so it
/// watches the per-second time and repaints in isolation.
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

/// The time text in the palette's primary gradient, applied with a
/// [ShaderMask] so the gradient spans just this text.
class _ShadedTime extends StatelessWidget {
  const _ShadedTime(this.text, {required this.style});

  final String text;
  final TextStyle style;

  @override
  Widget build(BuildContext context) {
    return ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: context.palette.primary.createShader,
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
