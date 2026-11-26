import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/protocol/messages.dart';
import 'package:hey_pi/state/events/app_event.dart';
import 'package:hey_pi/state/handler/event_dispatcher.dart';
import 'package:hey_pi/state/store/app_store.dart';
import 'package:hey_pi/state/store/server_store.dart';
import 'package:hey_pi/state/types/app_mode.dart';

void main() {
  test('pi-server\'s events change the server store, never the app\'s own mode', () {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final dispatch = container.read(eventDispatcherProvider).dispatch;
    SpeechState speech() => container.read(serverStoreProvider).speech;

    dispatch(const ServerSpeech(SpeechState.speaking));
    expect(speech(), SpeechState.speaking);
    dispatch(const ServerDisconnected()); // the link dropped mid-reply
    expect(speech(), SpeechState.idle);
    expect(container.read(appStoreProvider).mode, AppMode.dashboard);
  });
}
