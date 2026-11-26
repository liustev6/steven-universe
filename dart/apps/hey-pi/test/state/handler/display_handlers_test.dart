import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/state/store/app_store.dart';
import 'package:hey_pi/state/events/app_event.dart';
import 'package:hey_pi/state/handler/event_dispatcher.dart';
import 'package:hey_pi/state/types/app_mode.dart';

void main() {
  const active = AppMode.dashboard;
  const asleep = AppMode.idle;

  // Every display event from every display state, through the dispatcher.
  final cases = <(AppMode, AppModeEvent, AppMode)>[
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
      container.read(appStoreProvider.notifier).setMode(from);
      container.read(eventDispatcherProvider).dispatch(event);
      expect(container.read(appStoreProvider).mode, expected);
      // Cancel the idle timer before flutter_test checks for pending timers.
      container.dispose();
    });
  }

  test('cold start is active', () {
    expect(AppState.initial.mode, active);
  });
}
