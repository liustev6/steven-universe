import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'display_machine.dart';

abstract final class DisplayTiming {
  /// No touch for this long switches `active` → `sleep`.
  static const idleTimeout = Duration(minutes: 2);

  /// After waking, touches are kept away from controls for this long.
  static const wakeGuard = Duration(milliseconds: 300);
}

final displayProvider = NotifierProvider<DisplayNotifier, DisplayState>(
  DisplayNotifier.new,
);

/// Hosts [reduceDisplay] and owns the idle and wake-guard timers.
class DisplayNotifier extends Notifier<DisplayState> {
  Timer? _idleTimer;
  Timer? _guardTimer;

  @override
  DisplayState build() {
    ref.onDispose(() {
      _idleTimer?.cancel();
      _guardTimer?.cancel();
    });
    _restartIdleTimer();
    return DisplayState.initial;
  }

  /// Call on every touch anywhere on the screen.
  void userActivity() {
    _dispatch(const UserActivity());
    _restartIdleTimer();
  }

  /// Go to sleep immediately.
  void sleepNow() => _dispatch(const SleepRequested());

  void _dispatch(DisplayEvent event) {
    final previous = state;
    state = reduceDisplay(previous, event);
    if (previous.mode == DisplayMode.sleep &&
        state.mode == DisplayMode.active) {
      _guardTimer?.cancel();
      _guardTimer = Timer(
        DisplayTiming.wakeGuard,
        () => _dispatch(const WakeGuardElapsed()),
      );
    }
  }

  void _restartIdleTimer() {
    _idleTimer?.cancel();
    _idleTimer = Timer(
      DisplayTiming.idleTimeout,
      () => _dispatch(const IdleTimeout()),
    );
  }
}
