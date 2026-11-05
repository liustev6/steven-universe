import 'package:flutter_test/flutter_test.dart';
import 'package:home_panel/state/display_machine.dart';

void main() {
  const active = DisplayState(mode: DisplayMode.active);
  const guarded = DisplayState(mode: DisplayMode.active, inputBlocked: true);
  const asleep = DisplayState(mode: DisplayMode.sleep, inputBlocked: true);

  final cases = <(DisplayState, DisplayEvent, DisplayState)>[
    (active, const UserActivity(), active),
    (active, const IdleTimeout(), asleep),
    (active, const WakeGuardElapsed(), active),
    (active, const SleepRequested(), asleep),
    (guarded, const SleepRequested(), asleep),
    (asleep, const SleepRequested(), asleep),
    (guarded, const UserActivity(), guarded),
    (guarded, const IdleTimeout(), asleep),
    (guarded, const WakeGuardElapsed(), active),
    (asleep, const UserActivity(), guarded),
    (asleep, const IdleTimeout(), asleep),
    (asleep, const WakeGuardElapsed(), asleep),
  ];

  for (final (from, event, expected) in cases) {
    test('$from + ${event.runtimeType} → $expected', () {
      expect(reduceDisplay(from, event), expected);
    });
  }

  test('cold start is active with input allowed', () {
    expect(DisplayState.initial, active);
  });
}
