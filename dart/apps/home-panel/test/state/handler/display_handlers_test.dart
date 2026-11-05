import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_panel/state/app_state.dart';
import 'package:home_panel/state/events/app_event.dart';
import 'package:home_panel/state/handler/event_dispatcher.dart';
import 'package:home_panel/state/types/display_state.dart';

void main() {
  const active = DisplayState(mode: DisplayMode.active);
  const asleep = DisplayState(mode: DisplayMode.sleep);

  // Every display event from every display state, through the dispatcher.
  final cases = <(DisplayState, DisplayEvent, DisplayState)>[
    (active, const UserUncategorizedClick(), active),
    (active, const IdleTimeout(), asleep),
    (active, const SleepClick(), asleep),
    (asleep, const UserUncategorizedClick(), active),
    (asleep, const IdleTimeout(), asleep),
    (asleep, const SleepClick(), asleep),
  ];

  for (final (from, event, expected) in cases) {
    testWidgets('$from + ${event.runtimeType} → $expected', (tester) async {
      final container = ProviderContainer();
      container.read(appStoreProvider.notifier).setDisplay(from);
      container.read(eventDispatcherProvider).dispatch(event);
      expect(container.read(appStoreProvider).display, expected);
      // Cancel the idle timer before flutter_test checks for pending timers.
      container.dispose();
    });
  }

  test('cold start is active', () {
    expect(DisplayState.initial, active);
  });
}
