import 'package:flutter/material.dart';

import '../widgets/assistant_widget.dart';
import '../widgets/clock_widget.dart';
import '../widgets/home_controls_widget.dart';
import '../widgets/placeholder_tile.dart';
import '../widgets/sleep_tile.dart';
import '../widgets/weather_widget.dart';
import 'panel_grid.dart';

/// The dashboard layout: which widgets appear and where.
///
/// The grid is 12 columns of square cells (12 × 7 at 1024×600). Each entry
/// gives the column and row of a widget's top-left cell, counting from 0; the
/// widget's size comes from the widget itself. Placements that don't fit or
/// overlap are skipped by [validPlacements].
///
/// To add a widget: create a `PanelWidget` subclass and place it here.
/// `PlaceholderTile`s stand in for widgets that don't exist yet.
///
/// ```
///      0  1  2  3 | 4  5  6  7 | 8  9  10 | 11
/// r0   Clock      | Home       | Calendar | Temp
/// r1              |            |          | Humid
/// r2   Weather 3×2|UV|         |          | Door
/// r3              |1×|         | Media    | Vol
/// r4   Forecast 3×1|3| Assistant |          |
/// r5   Hourly 4×1 |            | Camera | Energy
/// r6   Sun  | Air | Sl Lk Fn Sc|        |
/// ```
const dashboardLayout = <PanelPlacement>[
  // Columns 0–3.
  PanelPlacement(column: 0, row: 0, widget: ClockWidget()), // 4×2
  PanelPlacement(column: 0, row: 2, widget: WeatherWidget()), // 3×2
  PanelPlacement(
    column: 0,
    row: 4,
    widget: PlaceholderTile(
      'Forecast',
      icon: Icons.event_note_outlined,
      columns: 3,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 3,
    row: 2,
    widget: PlaceholderTile(
      'UV',
      icon: Icons.wb_sunny_outlined,
      columns: 1,
      rows: 3,
    ),
  ),
  PanelPlacement(
    column: 0,
    row: 5,
    widget: PlaceholderTile(
      'Hourly',
      icon: Icons.schedule,
      columns: 4,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 0,
    row: 6,
    widget: PlaceholderTile(
      'Sunrise',
      icon: Icons.wb_twilight,
      columns: 2,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 2,
    row: 6,
    widget: PlaceholderTile('Air', icon: Icons.air, columns: 2, rows: 1),
  ),

  // Columns 4–7.
  PanelPlacement(column: 4, row: 0, widget: HomeControlsWidget()), // 4×4
  PanelPlacement(column: 4, row: 4, widget: AssistantWidget()), // 4×2
  PanelPlacement(column: 4, row: 6, widget: SleepTile()), // 1×1
  PanelPlacement(
    column: 5,
    row: 6,
    widget: PlaceholderTile(
      'Lock',
      icon: Icons.lock_outline,
      columns: 1,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 6,
    row: 6,
    widget: PlaceholderTile(
      'Fan',
      icon: Icons.mode_fan_off_outlined,
      columns: 1,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 7,
    row: 6,
    widget: PlaceholderTile(
      'Scene',
      icon: Icons.auto_awesome_outlined,
      columns: 1,
      rows: 1,
    ),
  ),

  // Columns 8–11.
  PanelPlacement(
    column: 8,
    row: 0,
    widget: PlaceholderTile(
      'Calendar',
      icon: Icons.calendar_today_outlined,
      columns: 3,
      rows: 3,
    ),
  ),
  PanelPlacement(
    column: 11,
    row: 0,
    widget: PlaceholderTile(
      'Temperature',
      icon: Icons.thermostat_outlined,
      columns: 1,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 11,
    row: 1,
    widget: PlaceholderTile(
      'Humidity',
      icon: Icons.water_drop_outlined,
      columns: 1,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 11,
    row: 2,
    widget: PlaceholderTile(
      'Door',
      icon: Icons.door_front_door_outlined,
      columns: 1,
      rows: 1,
    ),
  ),
  PanelPlacement(
    column: 8,
    row: 3,
    widget: PlaceholderTile(
      'Media',
      icon: Icons.music_note_outlined,
      columns: 3,
      rows: 2,
    ),
  ),
  PanelPlacement(
    column: 11,
    row: 3,
    widget: PlaceholderTile(
      'Volume',
      icon: Icons.volume_up_outlined,
      columns: 1,
      rows: 2,
    ),
  ),
  PanelPlacement(
    column: 8,
    row: 5,
    widget: PlaceholderTile(
      'Camera',
      icon: Icons.videocam_outlined,
      columns: 2,
      rows: 2,
    ),
  ),
  PanelPlacement(
    column: 10,
    row: 5,
    widget: PlaceholderTile(
      'Energy',
      icon: Icons.bolt_outlined,
      columns: 2,
      rows: 2,
    ),
  ),
];
