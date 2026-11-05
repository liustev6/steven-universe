import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../state/panel_providers.dart';
import '../ui/glass.dart';
import '../ui/sizing.dart';
import '../ui/theme.dart';

/// Top status bar. The pi-agent connection isn't built yet, so it always
/// reports offline for now. A sleep button at the right end switches to the
/// sleep clock immediately.
class StatusStrip extends StatelessWidget {
  const StatusStrip({super.key});

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodyMedium!
        .copyWith(fontSize: 15);
    return SizedBox(
      height: Sizing.statusStripHeight,
      child: GlassPanel(
        radius: Sizing.statusStripHeight / 2,
        padding: const EdgeInsets.only(left: Sizing.gap + 4, right: 6),
        child: Row(
          children: [
            Text('Home', style: style),
            const Spacer(),
            const _Dot(color: PanelColors.offline),
            const SizedBox(width: 8),
            Text('pi-agent offline', style: style),
            const SizedBox(width: 12),
            const SleepButton(),
          ],
        ),
      ),
    );
  }
}

/// Puts the panel to sleep immediately.
class SleepButton extends ConsumerWidget {
  const SleepButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return SizedBox(
      height: 32,
      child: TextButton.icon(
        onPressed: () => ref.read(displayProvider.notifier).sleepNow(),
        icon: const Icon(Icons.bedtime_outlined, size: 18),
        label: const Text('Sleep'),
        style: TextButton.styleFrom(
          foregroundColor: PanelColors.text,
          backgroundColor: GlassTokens.fillTop,
          shape: const StadiumBorder(
            side: BorderSide(color: GlassTokens.border),
          ),
          padding: const EdgeInsets.symmetric(horizontal: 14),
        ),
      ),
    );
  }
}

class _Dot extends StatelessWidget {
  const _Dot({required this.color});

  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}
