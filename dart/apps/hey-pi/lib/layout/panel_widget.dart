import 'package:flutter_riverpod/flutter_riverpod.dart';

/// Base class for every dashboard widget.
///
/// A panel widget declares its own size in grid cells; where it sits on the
/// dashboard comes from the layout file (`assets/layout.default.yaml`), which
/// refers to it by the name it is registered under in `widget_registry.dart`.
abstract class PanelWidget extends ConsumerWidget {
  const PanelWidget({super.key});

  /// Largest span allowed in either direction, in cells.
  static const maxSpan = 4;

  /// Width in grid cells (1–[maxSpan]).
  int get columns;

  /// Height in grid cells (1–[maxSpan]).
  int get rows;

  /// Whether this widget has a larger detail page it can expand into
  /// (opened through the router once that exists).
  bool get expandable => false;
}
