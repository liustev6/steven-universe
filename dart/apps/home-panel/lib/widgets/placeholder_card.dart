import 'package:flutter/material.dart';

import '../ui/glass.dart';
import '../ui/sizing.dart';
import '../ui/theme.dart';

/// Glass card for widgets whose content isn't built yet. Adapts to its size
/// in grid cells: icon only at 1×1, icon + title when one cell wide or tall,
/// and a full card (icon, size, title) for anything larger.
class PlaceholderCard extends StatelessWidget {
  const PlaceholderCard(
    this.title, {
    super.key,
    required this.icon,
    required this.columns,
    required this.rows,
  });

  final String title;
  final IconData icon;
  final int columns;
  final int rows;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final palette = context.palette;
    final iconWidget = Icon(icon, color: palette.secondary, size: 26);

    if (columns == 1 && rows == 1) {
      return GlassPanel(
        radius: Sizing.smallRadius,
        padding: EdgeInsets.zero,
        child: Center(child: iconWidget),
      );
    }

    if (columns == 1 || rows == 1) {
      final label = Text(
        title,
        style: text.bodyMedium!.copyWith(color: palette.primaryColor),
      );
      return GlassPanel(
        radius: Sizing.smallRadius,
        padding: const EdgeInsets.all(12),
        child: Center(
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: rows == 1
                ? Row(children: [iconWidget, const SizedBox(width: 8), label])
                : Column(
                    children: [iconWidget, const SizedBox(height: 6), label],
                  ),
          ),
        ),
      );
    }

    return GlassPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              iconWidget,
              const Spacer(),
              Text('$columns×$rows', style: text.bodyMedium),
            ],
          ),
          const Spacer(),
          Text(
            title,
            style: text.titleMedium,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            'Not built yet',
            style: text.bodyMedium,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }
}
