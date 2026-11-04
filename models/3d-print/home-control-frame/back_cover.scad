// Raspberry Pi 5 Home Assistant panel: back cover for the screen wrapper
//
// Flat plate with the same outline as the wrapper. Four M3 screws go
// through the corners into the wrapper's heat-set inserts.
//
// Print flat on the bed, no supports needed.
// Geometry is authored as seen from the BACK of the device ("left" = your
// left when looking at the back) and mirrored so it prints correctly.

include <frame_dims.scad>

/* [Cover] */
plate_t = 2.5;   // thickness

/* [M3 screws] */
hole_d = 3.4;    // M3 clearance hole
// More M3 holes, each [from left edge, from bottom edge] to the hole center,
// as seen from the back.
extra_holes = [
    [13.5, 17.3],                       // bottom left
    [outer_w - 13, 17.3],               // bottom right: 13 from right
    [outer_w - 13, outer_h - 14],       // top right: 13 from right, 14 from top
    [13.5, outer_h - 14],               // top left: 14 from top
    [outer_w / 2 - 22.5, 30],           // mount pair from the small plate:
    [outer_w / 2 + 22.5, 30],           //   45 apart, centered, 30 from bottom
];

/* [Island opening] */
// The screen's 18 mm center island pokes through this opening.
// Top-left corner measured from the cover's left and top edges (back view).
island_left  = 46;
island_top   = 7;
island_w     = 90;
island_h     = 63;
island_clear = 0.5;   // extra room on every side

/* [Openings] */
// Rectangular openings, each [left x, bottom y, right x, top y] in mm,
// measured from the cover's bottom-left corner as seen from the back.
cutouts = [
    [island_left - island_clear,
     outer_h - island_top - island_h - island_clear,
     island_left + island_w + island_clear,
     outer_h - island_top + island_clear],
];

$fn = 48;

module back_cover() {
    difference() {
        linear_extrude(plate_t) rounded_rect(outer_w, outer_h, corner_r);
        for (p = corner_holes)
            translate([p[0], p[1], -1])
                cylinder(d = hole_d, h = plate_t + 2);
        for (p = extra_holes)
            translate([p[0] - outer_w / 2, p[1] - outer_h / 2, -1])
                cylinder(d = hole_d, h = plate_t + 2);
        // Stand screws pass through here into the screen's back
        // (positions in frame_dims.scad)
        for (x = stand_x, y = stand_screw_y)
            translate([x - outer_w / 2, y - outer_h / 2, -1])
                cylinder(d = hole_d, h = plate_t + 2);
        for (c = cutouts)
            translate([c[0] - outer_w / 2, c[1] - outer_h / 2, -1])
                cube([c[2] - c[0], c[3] - c[1], plate_t + 2]);
    }
}

mirror([1, 0, 0]) back_cover();

echo(str("Cover: ", outer_w, " x ", outer_h, " x ", plate_t, " mm"));
