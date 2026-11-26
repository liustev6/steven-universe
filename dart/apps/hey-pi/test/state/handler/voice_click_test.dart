import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/connection/message_router.dart';
import 'package:hey_pi/connection/server_client.dart';
import 'package:hey_pi/state/events/app_event.dart';
import 'package:hey_pi/state/handler/event_dispatcher.dart';
import 'package:hey_pi/state/store/app_store.dart';

import '../../connection/fakes.dart';

void main() {
  test('the voice tile sends pi-server `wake`, and changes no store', () async {
    final server = FakeServer();
    final client = fastClient(server)..start();
    final container = ProviderContainer(
      overrides: [serverClientProvider.overrideWithValue(client)],
    );
    addTearDown(() async {
      container.dispose();
      await client.dispose();
    });
    await until(() => client.current == LinkStatus.connected);
    final before = container.read(appStoreProvider);

    container.read(eventDispatcherProvider).dispatch(const VoiceClick());

    expect(server.sockets.single.sent, [
      {'type': 'wake'},
    ]);
    expect(container.read(appStoreProvider), before);
  });

  test('not connected: the tap is dropped, not queued for later', () async {
    final server = FakeServer()..refuse = 1000;
    final client = fastClient(server)..start();
    final container = ProviderContainer(
      overrides: [serverClientProvider.overrideWithValue(client)],
    );
    addTearDown(() async {
      container.dispose();
      await client.dispose();
    });
    container.read(eventDispatcherProvider).dispatch(const VoiceClick());
    server.refuse = 0;
    await until(() => client.current == LinkStatus.connected);
    expect(server.sockets.single.sent, isEmpty);
  });
}
