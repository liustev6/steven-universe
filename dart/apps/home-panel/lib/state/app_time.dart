import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

final appTimeProvider = NotifierProvider<AppTime, DateTime>(AppTime.new);

/// The current time, updated every second on the second boundary. Widgets
/// watch this instead of running their own timers.
///
/// Anything that only displays minutes should watch [currentMinuteProvider]
/// instead, so it rebuilds once a minute rather than every second (the sleep
/// view must redraw at most once per minute).
class AppTime extends Notifier<DateTime> {
  Timer? _timer;

  @override
  DateTime build() {
    ref.onDispose(() => _timer?.cancel());
    final now = DateTime.now();
    _scheduleAfter(now);
    return now;
  }

  void _scheduleAfter(DateTime now) {
    final nextSecond = DateTime.fromMillisecondsSinceEpoch(
      (now.millisecondsSinceEpoch ~/ 1000 + 1) * 1000,
    );
    _timer = Timer(nextSecond.difference(now), () {
      final tick = DateTime.now();
      state = tick;
      _scheduleAfter(tick);
    });
  }
}

/// The current time truncated to the minute. Only changes when the minute
/// changes, so watchers rebuild once per minute.
final currentMinuteProvider = Provider<DateTime>((ref) {
  return ref.watch(
    appTimeProvider.select(
      (t) => DateTime(t.year, t.month, t.day, t.hour, t.minute),
    ),
  );
});
