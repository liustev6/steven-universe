import 'package:flutter/material.dart';

import '../layout/layout_config.dart';
import '../layout/panel_grid.dart';
import '../ui/glass.dart';
import '../ui/sizing.dart';

/// The `active` screen: the widgets from [dashboardLayout] on the
/// square-cell grid.
class DashboardView extends StatelessWidget {
  const DashboardView({super.key});

  /// 16 px at the sides and 8 px top/bottom: at 1024×600 this fits exactly
  /// 7 rows of 68 px cells (12 × 7), with ~14 px above and below the grid.
  static const padding = EdgeInsets.symmetric(
    horizontal: Sizing.gap,
    vertical: Sizing.gap / 2,
  );

  @override
  Widget build(BuildContext context) {
    return const GlassBackground(
      child: Padding(
        padding: padding,
        child: PanelGrid(gap: Sizing.gap, placements: dashboardLayout),
      ),
    );
  }
}
