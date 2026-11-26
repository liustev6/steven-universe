import '../../protocol/messages.dart';
import '../events/app_event.dart';
import '../types/app_mode.dart';
import '../store/handler_context.dart';

/// Handlers for every [SystemEvent] (events/system_events.dart).

/// No touch for the idle timeout: the idle screen.
void handleIdleTimeout(IdleTimeout event, HandlerContext ctx) {
  ctx.store.setMode(AppMode.idle);
}

// pi-server ------------------------------------------------------------------

/// pi-server's speech state: into the server store.
void handleServerSpeech(ServerSpeech event, HandlerContext ctx) {
  ctx.serverStore.setSpeech(event.speech);
}

/// No link to pi-server: no voice session the app can know of.
void handleServerDisconnected(ServerDisconnected event, HandlerContext ctx) {
  ctx.serverStore.setSpeech(SpeechState.idle);
}
