import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../layout/panel_widget.dart';
import 'placeholder_card.dart';

/// Voice assistant tile. Placeholder for now.
class AssistantWidget extends PanelWidget {
  const AssistantWidget({super.key});

  @override
  int get columns => 4;

  @override
  int get rows => 2;

  @override
  Widget build(BuildContext context, WidgetRef ref) => PlaceholderCard(
    'Assistant',
    icon: Icons.mic_none,
    columns: columns,
    rows: rows,
  );
}
