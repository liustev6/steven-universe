/// Display state (spec: "Display state"): just data. How it changes is
/// defined by the events in `state/events/` and their handlers in
/// `state/handler/`.
library;

enum DisplayMode { active, sleep }

class DisplayState {
  const DisplayState({required this.mode});

  /// Cold start shows the dashboard.
  static const initial = DisplayState(mode: DisplayMode.active);

  final DisplayMode mode;

  @override
  bool operator ==(Object other) => other is DisplayState && other.mode == mode;

  @override
  int get hashCode => mode.hashCode;

  @override
  String toString() => 'DisplayState($mode)';
}
