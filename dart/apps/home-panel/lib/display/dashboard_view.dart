import 'package:flutter/material.dart';

import '../layout/panel_grid.dart';
import '../ui/glass.dart';
import '../ui/sizing.dart';
import '../widgets/clock_widget.dart';
import '../widgets/status_strip.dart';

/// The `active` screen. The grid is hard-coded for now; it will come from the
/// layout file once that exists.
class DashboardView extends StatelessWidget {
  const DashboardView({super.key});

  @override
  Widget build(BuildContext context) {
    return const GlassBackground(
      child: Padding(
        padding: EdgeInsets.all(Sizing.gap),
        child: Column(
          children: [
            StatusStrip(),
            SizedBox(height: Sizing.gap),
            Expanded(
              child: PanelGrid(
                columns: 4,
                rows: 3,
                gap: Sizing.gap,
                items: [
                  PanelGridItem(
                    column: 0,
                    row: 0,
                    columnSpan: 2,
                    child: ClockWidget(),
                  ),
                  PanelGridItem(
                    column: 2,
                    row: 0,
                    columnSpan: 2,
                    rowSpan: 2,
                    child: _PlaceholderCard('Weather'),
                  ),
                  PanelGridItem(
                    column: 0,
                    row: 1,
                    columnSpan: 2,
                    rowSpan: 2,
                    child: _PlaceholderCard('Home'),
                  ),
                  PanelGridItem(
                    column: 2,
                    row: 2,
                    columnSpan: 2,
                    child: _PlaceholderCard('Assistant'),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _PlaceholderCard extends StatelessWidget {
  const _PlaceholderCard(this.title);

  final String title;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return GlassPanel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: text.titleMedium),
          const SizedBox(height: 6),
          Text('Not built yet', style: text.bodyMedium),
        ],
      ),
    );
  }
}
