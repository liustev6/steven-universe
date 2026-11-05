import '../events/app_event.dart';
import '../types/display_state.dart';
import 'event_dispatcher.dart';

/// Handlers for every [SystemEvent] (events/system_events.dart).

/// No touch for the idle timeout: go to sleep.
void handleIdleTimeout(IdleTimeout event, HandlerContext ctx) {
  ctx.store.setDisplay(const DisplayState(mode: DisplayMode.sleep));
}
