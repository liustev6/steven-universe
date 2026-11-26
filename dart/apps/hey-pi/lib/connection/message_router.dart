/// pi-server's messages and the link to it, as system events (docs/specs/flutter_ui.md "Message
/// router"): the router changes no store itself; the dispatcher sends each event to its handler,
/// which does. One switch over the sealed [ServerMessage]: a new message type nobody handles doesn't
/// compile.
library;

import 'dart:io' show Platform;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../protocol/messages.dart';
import '../state/events/app_event.dart';
import '../state/handler/event_dispatcher.dart';
import 'server_client.dart';

/// Where pi-server is: on the Pi, its own loopback. Another one (e.g. an SSH tunnel to the Pi)
/// with `--dart-define=PI_SERVER_URL=ws://…/ws`.
const piServerUrl = String.fromEnvironment(
  'PI_SERVER_URL',
  defaultValue: 'ws://127.0.0.1:8765/ws',
);

/// The one [ServerClient]. Not started under `flutter test`: tests override it with a fake.
final serverClientProvider = Provider<ServerClient>((ref) {
  final client = ServerClient(url: Uri.parse(piServerUrl));
  if (Platform.environment['FLUTTER_TEST'] != 'true') client.start();
  ref.onDispose(client.dispose);
  return client;
});

/// The link to pi-server, alive from app start (`PanelRoot` watches it).
final serverLinkProvider = Provider<void>((ref) {
  final client = ref.watch(serverClientProvider);
  final status = client.status.listen((s) {
    if (s != LinkStatus.connected) {
      ref.read(eventDispatcherProvider).dispatch(const ServerDisconnected());
    }
  });
  final messages = client.messages.listen((m) => route(ref, m));
  ref.onDispose(() {
    status.cancel();
    messages.cancel();
  });
});

void route(Ref ref, ServerMessage message) {
  switch (message) {
    case SpeechMessage(:final state):
      ref.read(eventDispatcherProvider).dispatch(ServerSpeech(state));
    case UnknownMessage():
      break; // logged by the client
  }
}
