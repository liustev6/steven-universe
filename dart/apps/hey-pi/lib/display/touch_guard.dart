import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/events/app_event.dart';
import '../state/handler/event_dispatcher.dart';

/// Wraps the whole screen. Every touch counts as user activity: it wakes the
/// panel if it's asleep and restarts the idle timer. Touches still reach the
/// controls underneath as normal.
class TouchGuard extends ConsumerWidget {
  const TouchGuard({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Listener(
      behavior: HitTestBehavior.translucent,
      onPointerDown: (_) => ref
          .read(eventDispatcherProvider)
          .dispatch(const UserUncategorizedClick()),
      child: child,
    );
  }
}
