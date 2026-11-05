import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../layout/panel_widget.dart';
import 'placeholder_card.dart';

/// A stand-in widget of any size, for trying out dashboard layouts before the
/// real widget exists.
class PlaceholderTile extends PanelWidget {
  const PlaceholderTile(
    this.title, {
    super.key,
    required this.icon,
    required this.columns,
    required this.rows,
  });

  final String title;
  final IconData icon;

  @override
  final int columns;

  @override
  final int rows;

  @override
  Widget build(BuildContext context, WidgetRef ref) =>
      PlaceholderCard(title, icon: icon, columns: columns, rows: rows);
}
