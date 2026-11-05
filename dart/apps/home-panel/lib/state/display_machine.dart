/// Display state machine (spec: "Display state").
///
/// Pure Dart: no Flutter or Riverpod imports, so it can be unit-tested
/// directly. Timers and other side effects live in `DisplayNotifier`.
library;

enum DisplayMode { active, sleep }

class DisplayState {
  const DisplayState({required this.mode, this.inputBlocked = false});

  /// Cold start shows the dashboard.
  static const initial = DisplayState(mode: DisplayMode.active);

  final DisplayMode mode;

  /// When true, touches still count as activity but must not reach controls.
  /// Set while asleep and for a short guard window after waking, so the tap
  /// that wakes the panel doesn't also press whatever is under the finger.
  final bool inputBlocked;

  @override
  bool operator ==(Object other) =>
      other is DisplayState &&
      other.mode == mode &&
      other.inputBlocked == inputBlocked;

  @override
  int get hashCode => Object.hash(mode, inputBlocked);

  @override
  String toString() => 'DisplayState($mode, inputBlocked: $inputBlocked)';
}

sealed class DisplayEvent {
  const DisplayEvent();
}

/// A touch anywhere on the screen.
final class UserActivity extends DisplayEvent {
  const UserActivity();
}

/// No touch for the configured idle timeout.
final class IdleTimeout extends DisplayEvent {
  const IdleTimeout();
}

/// An explicit request to go to sleep now (the debug sleep button).
final class SleepRequested extends DisplayEvent {
  const SleepRequested();
}

/// The post-wake guard window has passed.
final class WakeGuardElapsed extends DisplayEvent {
  const WakeGuardElapsed();
}

DisplayState reduceDisplay(DisplayState state, DisplayEvent event) {
  return switch ((state.mode, event)) {
    (DisplayMode.sleep, UserActivity()) => const DisplayState(
      mode: DisplayMode.active,
      inputBlocked: true,
    ),
    (DisplayMode.active, IdleTimeout() || SleepRequested()) =>
      const DisplayState(mode: DisplayMode.sleep, inputBlocked: true),
    (DisplayMode.active, WakeGuardElapsed()) => const DisplayState(
      mode: DisplayMode.active,
    ),
    _ => state,
  };
}
