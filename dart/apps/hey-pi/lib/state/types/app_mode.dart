/// The app's own mode (docs/specs/flutter_ui.md "App state"): app-only, never sent to or from
/// pi-server. How it changes is defined by the events in `state/events/` and their handlers in
/// `state/handler/`. What the screen shows also depends on pi-server's speech state:
/// `state/ui_mode.dart`.
library;

enum AppMode {
  /// The widgets. Cold start, and after any touch.
  dashboard,

  /// No touch for the idle timeout (or the Sleep tile): the idle screen.
  idle,
}
