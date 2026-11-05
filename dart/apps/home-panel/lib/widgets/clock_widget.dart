import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:intl/intl.dart';

import '../state/app_time.dart';
import '../ui/glass.dart';
import '../ui/theme.dart';

/// Date/time formats shared by the dashboard clock and the sleep clock.
abstract final class ClockFormats {
  static final time = DateFormat('h:mm');
  static final period = DateFormat('a');
  static final date = DateFormat('EEEE, MMMM d');
}

/// Time + date text for the dashboard clock card.
class ClockFace extends StatelessWidget {
  const ClockFace({
    super.key,
    required this.now,
    required this.timeSize,
    required this.color,
    required this.mutedColor,
    this.alignment = CrossAxisAlignment.start,
  });

  final DateTime now;
  final double timeSize;
  final Color color;
  final Color mutedColor;
  final CrossAxisAlignment alignment;

  @override
  Widget build(BuildContext context) {
    // Tabular figures keep the digits from shifting as the minutes change.
    const figures = [FontFeature.tabularFigures()];
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: alignment,
      children: [
        Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.baseline,
          textBaseline: TextBaseline.alphabetic,
          children: [
            Text(
              ClockFormats.time.format(now),
              style: TextStyle(
                fontSize: timeSize,
                fontWeight: FontWeight.w200,
                height: 1,
                color: color,
                fontFeatures: figures,
              ),
            ),
            SizedBox(width: timeSize * 0.08),
            Text(
              ClockFormats.period.format(now),
              style: TextStyle(
                fontSize: timeSize * 0.25,
                fontWeight: FontWeight.w400,
                color: mutedColor,
              ),
            ),
          ],
        ),
        SizedBox(height: timeSize * 0.12),
        Text(
          ClockFormats.date.format(now),
          style: TextStyle(
            fontSize: timeSize * 0.24,
            fontWeight: FontWeight.w400,
            color: mutedColor,
          ),
        ),
      ],
    );
  }
}

/// Dashboard clock card.
class ClockWidget extends ConsumerWidget {
  const ClockWidget({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final now = ref.watch(currentMinuteProvider);
    return GlassPanel(
      child: Align(
        alignment: Alignment.centerLeft,
        child: FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: ClockFace(
            now: now,
            timeSize: 88,
            color: PanelColors.text,
            mutedColor: PanelColors.textMuted,
          ),
        ),
      ),
    );
  }
}
