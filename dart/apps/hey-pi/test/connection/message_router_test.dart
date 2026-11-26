import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/connection/message_router.dart';
import 'package:hey_pi/protocol/messages.dart';
import 'package:hey_pi/state/store/server_store.dart';

import 'fakes.dart';

void main() {
  test('pi-server\'s speech state; idle while it isn\'t connected', () async {
    final server = FakeServer();
    final c = fastClient(server)..start();
    final container = ProviderContainer(
      overrides: [serverClientProvider.overrideWithValue(c)],
    );
    addTearDown(container.dispose);
    container.read(serverLinkProvider);
    SpeechState speech() => container.read(serverStoreProvider).speech;

    await until(() => server.sockets.isNotEmpty);
    server.sockets.single.say({'type': 'speech', 'state': 'speaking'});
    await until(() => speech() == SpeechState.speaking);

    await server.sockets.single
        .drop(); // pi-server gone mid-reply: nothing to show
    await until(() => speech() == SpeechState.idle);

    await until(() => server.sockets.length == 2);
    server.sockets.last.say({'type': 'speech', 'state': 'listening'});
    await until(() => speech() == SpeechState.listening);
    await c.dispose();
  });
}
