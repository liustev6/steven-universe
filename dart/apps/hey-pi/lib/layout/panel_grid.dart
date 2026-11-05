import 'dart:math' as math;

import 'package:flutter/widgets.dart';

import 'panel_widget.dart';

/// A [PanelWidget] placed on the grid: the cell of its top-left corner. Its
/// size comes from the widget ([PanelWidget.columns] × [PanelWidget.rows]).
class PanelPlacement {
  const PanelPlacement({
    required this.column,
    required this.row,
    required this.widget,
  });

  final int column;
  final int row;
  final PanelWidget widget;

  int get columnSpan => widget.columns;
  int get rowSpan => widget.rows;
}

/// Keeps the placements that fit a [columns] × [rows] grid. Skipped, with a
/// log message: spans outside 1–[PanelWidget.maxSpan], negative positions,
/// anything past the last column or row, and anything overlapping an earlier
/// placement. One bad placement never takes the dashboard down.
List<PanelPlacement> validPlacements(
  List<PanelPlacement> placements, {
  required int columns,
  required int rows,
}) {
  final taken = <(int, int)>{};
  final valid = <PanelPlacement>[];
  for (final p in placements) {
    final name = '${p.widget.runtimeType} at (${p.column}, ${p.row})';
    String? problem;
    if (p.columnSpan < 1 ||
        p.rowSpan < 1 ||
        p.columnSpan > PanelWidget.maxSpan ||
        p.rowSpan > PanelWidget.maxSpan) {
      problem =
          'is ${p.columnSpan}×${p.rowSpan}; '
          'spans must be 1–${PanelWidget.maxSpan}';
    } else if (p.column < 0 || p.row < 0) {
      problem = 'has a negative position';
    } else if (p.column + p.columnSpan > columns || p.row + p.rowSpan > rows) {
      problem = 'is outside the $columns×$rows grid';
    }
    final cells = {
      for (var c = p.column; c < p.column + p.columnSpan; c++)
        for (var r = p.row; r < p.row + p.rowSpan; r++) (c, r),
    };
    if (problem == null && cells.any(taken.contains)) {
      problem = 'overlaps another widget';
    }
    if (problem != null) {
      debugPrint('grid: $name $problem; skipped');
      continue;
    }
    taken.addAll(cells);
    valid.add(p);
  }
  return valid;
}

/// Cell size and row count for a grid of square cells.
class PanelGridMetrics {
  const PanelGridMetrics({
    required this.cellSize,
    required this.columns,
    required this.rows,
    required this.gap,
    required this.topInset,
  });

  /// Square cells: the width is split into [columns], and as many full rows
  /// as fit in the height are used. Leftover height is split evenly above
  /// and below the grid ([topInset]).
  factory PanelGridMetrics.compute(
    Size size, {
    required int columns,
    required double gap,
  }) {
    final cellSize = (size.width - gap * (columns - 1)) / columns;
    final rows = math.max(0, ((size.height + gap) / (cellSize + gap)).floor());
    final gridHeight = rows == 0 ? 0.0 : rows * cellSize + (rows - 1) * gap;
    return PanelGridMetrics(
      cellSize: cellSize,
      columns: columns,
      rows: rows,
      gap: gap,
      topInset: (size.height - gridHeight) / 2,
    );
  }

  final double cellSize;
  final int columns;
  final int rows;
  final double gap;
  final double topInset;

  double offsetOf(int cell) => cell * (cellSize + gap);
  double extentOf(int span) => span * cellSize + (span - 1) * gap;
}

/// Grid of square cells: a fixed number of [columns] across, and as many rows
/// as fit the available height. Draws the [placements] that pass
/// [validPlacements].
class PanelGrid extends StatelessWidget {
  const PanelGrid({
    super.key,
    this.columns = defaultColumns,
    required this.gap,
    required this.placements,
  });

  static const defaultColumns = 12;

  final int columns;
  final double gap;
  final List<PanelPlacement> placements;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final grid = PanelGridMetrics.compute(
          constraints.biggest,
          columns: columns,
          gap: gap,
        );
        final valid = validPlacements(
          placements,
          columns: grid.columns,
          rows: grid.rows,
        );
        return Stack(
          children: [
            for (final p in valid)
              Positioned(
                left: grid.offsetOf(p.column),
                top: grid.topInset + grid.offsetOf(p.row),
                width: grid.extentOf(p.columnSpan),
                height: grid.extentOf(p.rowSpan),
                child: p.widget,
              ),
          ],
        );
      },
    );
  }
}
