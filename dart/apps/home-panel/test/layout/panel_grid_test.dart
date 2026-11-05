import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:home_panel/layout/panel_grid.dart';
import 'package:home_panel/layout/panel_widget.dart';

/// Test widget of any size.
class _Box extends PanelWidget {
  const _Box(this.columns, this.rows, {super.key});

  @override
  final int columns;

  @override
  final int rows;

  @override
  Widget build(BuildContext context, WidgetRef ref) => const SizedBox.expand();
}

PanelPlacement _at(int column, int row, int columns, int rows) =>
    PanelPlacement(column: column, row: row, widget: _Box(columns, rows));

void main() {
  group('PanelGridMetrics', () {
    test('a 992×508 area is 12 × 6 square 68 px cells', () {
      final grid = PanelGridMetrics.compute(
        const Size(992, 508),
        columns: 12,
        gap: 16,
      );
      expect(grid.cellSize, 68);
      expect(grid.rows, 6);
      // 6 rows use 488 px; the 20 px left over is split above and below.
      expect(grid.topInset, 10);
    });

    test('dashboard area at 1024×600 is 12 × 7 square 68 px cells', () {
      // 1024×600 minus 16 px side and 8 px top/bottom padding.
      final grid = PanelGridMetrics.compute(
        const Size(992, 584),
        columns: 12,
        gap: 16,
      );
      expect(grid.cellSize, 68);
      expect(grid.rows, 7);
      // 7 rows use 572 px; the 12 px left over is split above and below.
      expect(grid.topInset, 6);
    });

    test('spans include the gaps between cells', () {
      final grid = PanelGridMetrics.compute(
        const Size(992, 508),
        columns: 12,
        gap: 16,
      );
      expect(grid.extentOf(1), 68);
      expect(grid.extentOf(6), 6 * 68 + 5 * 16);
      expect(grid.offsetOf(6), 6 * (68 + 16));
      // 12 columns end exactly at the right edge.
      expect(grid.offsetOf(0) + grid.extentOf(12), 992);
    });

    test('a taller area fits more rows of the same cell size', () {
      final grid = PanelGridMetrics.compute(
        const Size(992, 900),
        columns: 12,
        gap: 16,
      );
      expect(grid.cellSize, 68);
      expect(grid.rows, 10);
    });
  });

  group('validPlacements', () {
    List<(int, int)> positions(List<PanelPlacement> placements) =>
        validPlacements(
          placements,
          columns: 12,
          rows: 6,
        ).map((p) => (p.column, p.row)).toList();

    test('keeps placements that fit', () {
      expect(positions([_at(0, 0, 4, 2), _at(4, 0, 4, 4)]), [(0, 0), (4, 0)]);
    });

    test('skips overlaps, keeping the earlier placement', () {
      expect(positions([_at(0, 0, 4, 2), _at(2, 1, 4, 2)]), [(0, 0)]);
    });

    test('skips placements past the last column or row', () {
      expect(positions([_at(10, 0, 4, 2), _at(0, 5, 2, 2)]), isEmpty);
    });

    test('skips negative positions', () {
      expect(positions([_at(-1, 0, 2, 2)]), isEmpty);
    });

    test(
      'skips spans larger than ${PanelWidget.maxSpan} or smaller than 1',
      () {
        expect(positions([_at(0, 0, 5, 1), _at(0, 2, 0, 1)]), isEmpty);
      },
    );
  });

  testWidgets('places widgets as squares at their cells', (tester) async {
    tester.view.physicalSize = const Size(1024, 600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      const ProviderScope(
        child: Directionality(
          textDirection: TextDirection.ltr,
          child: Center(
            child: SizedBox(
              width: 992,
              height: 508,
              child: PanelGrid(
                gap: 16,
                placements: [
                  PanelPlacement(
                    column: 6,
                    row: 2,
                    widget: _Box(2, 2, key: Key('box')),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
    final grid = tester.getTopLeft(find.byType(PanelGrid));
    final rect = tester.getRect(find.byKey(const Key('box')));
    expect(rect.width, 2 * 68 + 16);
    expect(rect.height, rect.width);
    expect(rect.left - grid.dx, 6 * (68 + 16));
    expect(rect.top - grid.dy, 10 + 2 * (68 + 16));
  });
}
