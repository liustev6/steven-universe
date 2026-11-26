import 'package:flutter/material.dart';

import '../protocol/messages.dart';
import '../ui/theme.dart';

/// The screen while the voice assistant listens or speaks. A plain placeholder: its design comes
/// later.
class SpeechView extends StatelessWidget {
  const SpeechView(this.speech, {super.key});

  final SpeechState speech;

  @override
  Widget build(BuildContext context) {
    final palette = context.palette;
    final listening = speech == SpeechState.listening;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            listening ? Icons.mic : Icons.graphic_eq,
            size: 96,
            color: palette.primaryColor,
          ),
          const SizedBox(height: 16),
          Text(
            listening ? 'Listening…' : 'Speaking…',
            style: Theme.of(context).textTheme.headlineMedium,
          ),
        ],
      ),
    );
  }
}
