// Raspberry Pi 5 Home Assistant panel: small back plate
//
// Flat plate with four M3 corner holes and a port opening on the left.
// Print flat on the bed, no supports needed.
//
// The middle M3 mounting pair (45 mm apart) moved to the main back cover.

/* [Plate] */
plate_w  = 90;    // width
plate_h  = 63;    // height
plate_t  = 2;     // thickness
corner_r = 3;     // corner radius

/* [M3 holes] */
hole_d = 3.4;     // M3 clearance hole
// Each [from left edge, from bottom edge] to the hole center
holes = [
    [12, 3],                          // bottom left
    [12, plate_h - 8],                // top left: 12 from left, 8 from top
    [plate_w - 3.5, plate_h - 8],     // top right: 3.5 from right, 8 from top
    [plate_w - 3.5, 3],               // bottom right: 3.5 from right, 3 from bottom
];

/* [Port cutouts] */
// Rectangular openings, each [left x, bottom y, right x, top y] from the
// plate's bottom-left corner.
cutouts = [
    [2, 3, 9.5, plate_h - 7],         // output port: 2-9.5 from left, 3 from bottom to 7 from top
];

$fn = 48;

module rounded_rect(w, h, r) {
    translate([r, r]) offset(r) square([w - 2 * r, h - 2 * r]);
}

module back_plate() {
    difference() {
        linear_extrude(plate_t) rounded_rect(plate_w, plate_h, corner_r);
        for (p = holes)
            translate([p[0], p[1], -1])
                cylinder(d = hole_d, h = plate_t + 2);
        for (c = cutouts)
            translate([c[0], c[1], -1])
                cube([c[2] - c[0], c[3] - c[1], plate_t + 2]);
    }
}

back_plate();

echo(str("Plate: ", plate_w, " x ", plate_h, " x ", plate_t, " mm"));
