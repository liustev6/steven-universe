import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../layout/panel_widget.dart';
import 'placeholder_card.dart';

/// Home Assistant light/scene toggles. Placeholder for now.
class HomeControlsWidget extends PanelWidget {
  const HomeControlsWidget({super.key});

  @override
  int get columns => 4;

  @override
  int get rows => 4;

  @override
  Widget build(BuildContext context, WidgetRef ref) => PlaceholderCard(
    'Home',
    icon: Icons.lightbulb_outline,
    columns: columns,
    rows: rows,
  );
}
