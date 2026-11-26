/// The server store (docs/specs/flutter_ui.md "App state"): what pi-server tells the app, one field
/// per message of the protocol. Changed only by handlers (`state/handler/system_handler.dart`), from
/// the system events the router raises; the app's own state is the app store's.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../protocol/messages.dart';

class ServerState {
  const ServerState({this.speech = SpeechState.idle});

  static const initial = ServerState();

  /// The voice assistant: `idle` when pi-server says so, or when it isn't connected.
  final SpeechState speech;

  ServerState copyWith({SpeechState? speech}) =>
      ServerState(speech: speech ?? this.speech);

  @override
  bool operator ==(Object other) =>
      other is ServerState && other.speech == speech;

  @override
  int get hashCode => speech.hashCode;

  @override
  String toString() => 'ServerState($speech)';
}

final serverStoreProvider = NotifierProvider<ServerStore, ServerState>(
  ServerStore.new,
);

/// The store: [ServerState] and its setters (handlers only).
class ServerStore extends Notifier<ServerState> {
  @override
  ServerState build() => ServerState.initial;

  void setSpeech(SpeechState speech) => state = state.copyWith(speech: speech);
}
