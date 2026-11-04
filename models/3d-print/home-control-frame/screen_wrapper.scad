// Raspberry Pi 5 Home Assistant panel: screen wrapper (front bezel)
//
// The screen drops in from the back and rests against the front plate.
// A back cover (next part, holds the Pi 5) screws into the 4 corner
// heat-set inserts on the back rim.
//
// Print face-down (front plate on the bed), no supports needed.
// Geometry is authored as seen from the FRONT ("left" = your left when
// looking at the screen) and mirrored at the end so it prints correctly.

// Screen size, wall thickness and corner holes are shared with the
// back cover in frame_dims.scad.
include <frame_dims.scad>

/* [Screen module] */
screen_d = 16.5;   // pocket depth (front plate to back rim)

/* [Visible window: bezel overlap from each screen edge] */
// Measure from each glass edge to where the lit pixels start.
// Active area is 6.0 x 3.28 in (152.4 x 83.3 mm); this window is 153 x 85.
win_left   = 5;
win_right  = 5;
win_top    = 7;
win_bottom = 18;

/* [Shell] */
face         = 2;    // front plate thickness
edge_chamfer = 1.2;  // chamfer on the outer front edge
win_chamfer  = 1;    // chamfer on the window edge (front side)
back_extra   = 0;    // extra wall depth behind the screen

/* [Back cover mounting] */
inserts      = true;
insert_d     = 4.0;  // hole for M3 x 5 heat-set insert (check your inserts)
insert_depth = 6;

/* [Side cutouts] */
// Notches in the wall from the back rim, for side-facing ports.
// Each entry: [side, offset from that edge's center, width, height from back]
// side is "left", "right", "top" or "bottom" (as seen from the front).
// Example: [["right", 20, 30, 10]]
cutouts = [];

$fn = 48;

outer_d  = face + screen_d + back_extra;

assert(corner_r > edge_chamfer, "corner_r must be larger than edge_chamfer");
assert(!inserts || min(wall, side_wall) >= insert_d + 2, "wall too thin for the inserts");

module outer_body() {
    hull() {
        linear_extrude(0.01)
            rounded_rect(outer_w - 2 * edge_chamfer, outer_h - 2 * edge_chamfer,
                         corner_r - edge_chamfer);
        translate([0, 0, edge_chamfer])
            linear_extrude(outer_d - edge_chamfer)
                rounded_rect(outer_w, outer_h, corner_r);
    }
}

module screen_pocket() {
    translate([0, 0, face])
        linear_extrude(outer_d) {
            square([pocket_w, pocket_h], center = true);
            // Corner reliefs so the screen's sharp corners seat fully
            for (sx = [-1, 1], sy = [-1, 1])
                translate([sx * pocket_w / 2, sy * pocket_h / 2]) circle(r = 1);
        }
}

module window() {
    x0 = -screen_w / 2 + win_left;
    x1 =  screen_w / 2 - win_right;
    y0 = -screen_h / 2 + win_bottom;
    y1 =  screen_h / 2 - win_top;
    translate([(x0 + x1) / 2, (y0 + y1) / 2, 0])
        hull() {
            translate([0, 0, -0.01])
                linear_extrude(0.01)
                    square([x1 - x0 + 2 * win_chamfer, y1 - y0 + 2 * win_chamfer], center = true);
            translate([0, 0, win_chamfer])
                linear_extrude(face)
                    square([x1 - x0, y1 - y0], center = true);
        }
}

module insert_holes() {
    for (p = corner_holes)
        translate([p[0], p[1], outer_d - insert_depth])
            cylinder(d = insert_d, h = insert_depth + 1);
}

module side_cutout(c) {
    side = c[0]; offs = c[1]; w = c[2]; h = c[3];
    z = outer_d - h;
    if (side == "left" || side == "right")
        translate([(side == "right" ? 1 : -1) * (pocket_w + side_wall) / 2, offs, z + (h + 1) / 2])
            cube([side_wall + 2, w, h + 1], center = true);
    else
        translate([offs, (side == "top" ? 1 : -1) * (pocket_h + wall) / 2, z + (h + 1) / 2])
            cube([w, wall + 2, h + 1], center = true);
}

module screen_wrapper() {
    difference() {
        outer_body();
        screen_pocket();
        window();
        if (inserts) insert_holes();
        for (c = cutouts) side_cutout(c);
    }
}

mirror([1, 0, 0]) screen_wrapper();

echo(str("Outer size: ", outer_w, " x ", outer_h, " x ", outer_d, " mm"));
