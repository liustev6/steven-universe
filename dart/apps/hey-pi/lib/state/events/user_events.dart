part of 'app_event.dart';

/// Things the user did. Every one restarts the idle timer (see the
/// dispatcher). Handlers: `handler/user_handler.dart`.

// Display ------------------------------------------------------------------

/// A touch anywhere on the screen. Wakes the panel if it's asleep.
/// Handler: `handleUserUncategorizedClick`.
final class UserUncategorizedClick extends UserEvent implements DisplayEvent {
  const UserUncategorizedClick();
}

/// The Sleep tile was tapped: go to sleep now. Handler: `handleSleepClick`.
final class SleepClick extends UserEvent implements DisplayEvent {
  const SleepClick();
}

// Settings -----------------------------------------------------------------

/// The theme tile was tapped: flip between dark and light mode.
/// Handler: `handleThemeModeClick`.
final class ThemeModeClick extends UserEvent {
  const ThemeModeClick();
}
