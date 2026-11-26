import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../connection/message_router.dart';
import '../../protocol/messages.dart';
import '../events/app_event.dart';
import 'app_store.dart';
import 'server_store.dart';

/// Everything a handler may use: read the stores, change them through their
/// setters, raise further events, and send pi-server a message. Handlers are
/// the only code that changes a store.
class HandlerContext {
  HandlerContext(this._ref, {required this.dispatch});

  final Ref _ref;

  /// Raise another event (goes through the dispatcher like any other).
  final void Function(AppEvent event) dispatch;

  /// The app store: the app's own state.
  AppState get state => _ref.read(appStoreProvider);

  /// The app store's setters.
  AppStore get store => _ref.read(appStoreProvider.notifier);

  /// The server store: what pi-server tells the app.
  ServerState get server => _ref.read(serverStoreProvider);

  /// The server store's setters.
  ServerStore get serverStore => _ref.read(serverStoreProvider.notifier);

  /// Send pi-server [message] now (dropped if not connected).
  void send(ScreenMessage message) =>
      _ref.read(serverClientProvider).send(message);
}
