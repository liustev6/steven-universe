import 'package:flutter/widgets.dart';

/// A grid of equal cells inside a widget, for laying out its contents.
/// Unlike the dashboard grid, cells don't have to be square.
///
/// ```dart
/// final grid = CellGrid(size, rows: 12, columns: 2, columnGap: 2);
/// Positioned.fromRect(rect: grid.rect(1, 3, rowSpan: 9), child: ...);
/// ```
class CellGrid {
  CellGrid(
    Size size, {
    required this.rows,
    required this.columns,
    this.rowGap = 0,
    this.columnGap = 0,
  }) : row = (size.height - rowGap * (rows - 1)) / rows,
       column = (size.width - columnGap * (columns - 1)) / columns;

  final int rows;
  final int columns;
  final double rowGap;
  final double columnGap;

  /// Height of one row.
  final double row;

  /// Width of one column.
  final double column;

  /// The area covered by the cell at [col], [r], spanning [columnSpan] ×
  /// [rowSpan] cells (gaps between spanned cells included).
  Rect rect(int col, int r, {int columnSpan = 1, int rowSpan = 1}) =>
      Rect.fromLTWH(
        col * (column + columnGap),
        r * (row + rowGap),
        columnSpan * column + (columnSpan - 1) * columnGap,
        rowSpan * row + (rowSpan - 1) * rowGap,
      );
}
