import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../layout/panel_widget.dart';
import '../state/events/app_event.dart';
import '../state/handler/event_dispatcher.dart';
import '../ui/glass.dart';
import '../ui/sizing.dart';
import '../ui/theme.dart';

/// 1×1 voice button: as if the wake word were heard. pi-server opens a voice
/// session (or barges in over a reply); the screen follows its speech state.
class VoiceTile extends PanelWidget {
  const VoiceTile({super.key});

  @override
  int get columns => 1;

  @override
  int get rows => 1;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () =>
          ref.read(eventDispatcherProvider).dispatch(const VoiceClick()),
      child: GlassPanel(
        radius: Sizing.smallRadius,
        padding: EdgeInsets.zero,
        child: Center(
          child: Icon(
            Icons.mic_none,
            color: context.palette.primaryColor,
            size: 26,
            semanticLabel: 'Talk to Hey Pi',
          ),
        ),
      ),
    );
  }
}
