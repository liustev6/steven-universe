import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/panel_providers.dart';

/// Wraps the whole screen. Every touch counts as user activity (wakes the
/// panel, resets the idle timer); while the display state says input is
/// blocked, touches don't reach the controls underneath.
class TouchGuard extends ConsumerWidget {
  const TouchGuard({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final blocked = ref.watch(displayProvider.select((s) => s.inputBlocked));
    return Listener(
      behavior: HitTestBehavior.translucent,
      onPointerDown: (_) => ref.read(displayProvider.notifier).userActivity(),
      child: IgnorePointer(ignoring: blocked, child: child),
    );
  }
}
