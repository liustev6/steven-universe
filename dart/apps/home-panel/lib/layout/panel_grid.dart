import 'package:flutter/widgets.dart';

/// A widget placed on [PanelGrid] by cell position and span.
class PanelGridItem {
  const PanelGridItem({
    required this.column,
    required this.row,
    this.columnSpan = 1,
    this.rowSpan = 1,
    required this.child,
  });

  final int column;
  final int row;
  final int columnSpan;
  final int rowSpan;
  final Widget child;
}

/// Fixed grid of equal cells separated by [gap]. Items are positioned by
/// cell coordinates, which is what the layout file will describe.
class PanelGrid extends StatelessWidget {
  const PanelGrid({
    super.key,
    required this.columns,
    required this.rows,
    required this.gap,
    required this.items,
  });

  final int columns;
  final int rows;
  final double gap;
  final List<PanelGridItem> items;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final cellWidth =
            (constraints.maxWidth - gap * (columns - 1)) / columns;
        final cellHeight = (constraints.maxHeight - gap * (rows - 1)) / rows;
        return Stack(
          children: [
            for (final item in items)
              Positioned(
                left: item.column * (cellWidth + gap),
                top: item.row * (cellHeight + gap),
                width:
                    item.columnSpan * cellWidth + (item.columnSpan - 1) * gap,
                height: item.rowSpan * cellHeight + (item.rowSpan - 1) * gap,
                child: item.child,
              ),
          ],
        );
      },
    );
  }
}
