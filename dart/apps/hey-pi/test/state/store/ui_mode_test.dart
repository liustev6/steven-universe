import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/protocol/messages.dart';
import 'package:hey_pi/state/store/app_store.dart';
import 'package:hey_pi/state/store/server_store.dart';
import 'package:hey_pi/state/types/app_mode.dart';
import 'package:hey_pi/state/store/ui_mode.dart';

void main() {
  test(
    'speech over the app\'s own mode; the app\'s mode only when speech is idle',
    () {
      const table = {
        (AppMode.dashboard, SpeechState.idle): UiMode.dashboard,
        (AppMode.idle, SpeechState.idle): UiMode.idle,
        (AppMode.dashboard, SpeechState.listening): UiMode.listening,
        (AppMode.idle, SpeechState.listening): UiMode.listening,
        (AppMode.dashboard, SpeechState.speaking): UiMode.speaking,
        (AppMode.idle, SpeechState.speaking): UiMode.speaking,
      };
      for (final MapEntry(key: (app, speech), value: expected)
          in table.entries) {
        expect(uiModeOf(app, speech), expected, reason: '$app + $speech');
      }
    },
  );

  test('derived from both stores, never stored', () {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    expect(container.read(uiModeProvider), UiMode.dashboard);
    container.read(appStoreProvider.notifier).setMode(AppMode.idle);
    expect(container.read(uiModeProvider), UiMode.idle);
    container
        .read(serverStoreProvider.notifier)
        .setSpeech(SpeechState.listening);
    expect(container.read(uiModeProvider), UiMode.listening);
    container.read(serverStoreProvider.notifier).setSpeech(SpeechState.idle);
    expect(
      container.read(uiModeProvider),
      UiMode.idle,
    ); // back to the app's own mode
  });
}
