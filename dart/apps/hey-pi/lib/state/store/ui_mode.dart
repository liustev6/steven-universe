/// What the screen shows (docs/specs/flutter_ui.md "App state"), derived, never stored: pi-server's
/// speech state when it isn't idle, else the app's own mode.
library;

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../protocol/messages.dart';
import 'app_store.dart';
import 'server_store.dart';
import '../types/app_mode.dart';

enum UiMode { dashboard, idle, listening, speaking }

UiMode uiModeOf(AppMode app, SpeechState speech) => switch (speech) {
  SpeechState.listening => UiMode.listening,
  SpeechState.speaking => UiMode.speaking,
  SpeechState.idle => switch (app) {
    AppMode.dashboard => UiMode.dashboard,
    AppMode.idle => UiMode.idle,
  },
};

final uiModeProvider = Provider<UiMode>(
  (ref) => uiModeOf(
    ref.watch(appStoreProvider.select((s) => s.mode)),
    ref.watch(serverStoreProvider.select((s) => s.speech)),
  ),
);
