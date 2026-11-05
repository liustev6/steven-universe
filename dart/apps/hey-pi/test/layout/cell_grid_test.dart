import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:hey_pi/layout/cell_grid.dart';

void main() {
  final grid = CellGrid(
    const Size(202, 120),
    rows: 12,
    columns: 2,
    rowGap: 0,
    columnGap: 2,
  );

  test('cell sizes account for the gaps', () {
    expect(grid.column, 100); // (202 − 2) / 2
    expect(grid.row, 10); // 120 / 12
  });

  test('rect places and spans cells', () {
    expect(grid.rect(0, 0), const Rect.fromLTWH(0, 0, 100, 10));
    expect(grid.rect(1, 3, rowSpan: 9), const Rect.fromLTWH(102, 30, 100, 90));
    expect(
      grid.rect(0, 0, columnSpan: 2, rowSpan: 3),
      const Rect.fromLTWH(0, 0, 202, 30),
    );
  });

  test('row gaps are included between spanned rows', () {
    final gapped = CellGrid(
      const Size(100, 100),
      rows: 4,
      columns: 1,
      rowGap: 4,
    );
    expect(gapped.row, 22); // (100 − 3 × 4) / 4
    expect(gapped.rect(0, 1, rowSpan: 2), const Rect.fromLTWH(0, 26, 100, 48));
  });
}
