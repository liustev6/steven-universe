import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/state/events/app_event.dart';

void main() {
  test('each event has one source type and its effect types', () {
    final cases = <(AppEvent, {bool user, bool display})>[
      (const UserUncategorizedClick(), user: true, display: true),
      (const SleepClick(), user: true, display: true),
      (const ThemeModeClick(), user: true, display: false),
      (const IdleTimeout(), user: false, display: true),
    ];
    for (final (event, :user, :display) in cases) {
      final name = event.runtimeType.toString();
      expect(event is UserEvent, user, reason: '$name is UserEvent');
      expect(event is SystemEvent, !user, reason: '$name is SystemEvent');
      expect(event is DisplayEvent, display, reason: '$name is DisplayEvent');
    }
  });
}
