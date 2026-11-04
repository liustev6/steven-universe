// Raspberry Pi 5 Home Assistant panel: desk stand (print 2, both identical)
//
// Side profile: a flat base on the desk, a seat that lifts the screen,
// a front lip that hooks over the bottom of the bezel, and a back post that
// the back cover screws onto. The screen leans back by `tilt`.
//
// M3 screws go in from the back of the post, through the back cover and
// into the screen's back. The heads sit in a recess in the post.
//
// Prints lying on its side exactly as exported, no supports needed.

include <frame_dims.scad>

/* [Screen] */
device_d = 21;     // wrapper (2 face + 16.5 pocket = 18.5) + back cover (2.5)
tilt     = 15;     // lean back from vertical, degrees
lift     = 20;     // lowest point of the screen above the desk
fit_c    = 1;      // clearance across the screen's thickness

/* [Stand] */
stand_w   = 20;    // width of each stand
lip_t     = 4;     // front lip thickness
lip_h     = 10;    // how far the lip reaches up the front bezel
post_t    = 7;     // back post thickness
post_h    = 45;    // how far the post reaches up the back cover
base_t    = 6;     // base thickness on the desk
base_rear = 80;    // base length behind the post, keeps it from tipping
round_r   = 1.5;   // rounding on the outside corners of the side profile
edge_r    = 1;     // rounding on the edges of the two flat sides

/* [Rubber feet] */
foot_d     = 10.5;  // recess for 10 mm adhesive bumpers
foot_depth = 1.5;
foot_inset = 8;     // recess center from the front and back ends of the base

/* [M3 screws] */
hole_d     = 3.4;  // M3 clearance hole
head_d     = 6.2;  // recess for the screw head
head_depth = 3;    // recess depth into the back of the post

$fn = 48;

slot_d = device_d + fit_c;

// Screen frame (X across the thickness from the front face, Y up the screen
// from its bottom edge) into desk frame (x toward the back, y up).
module to_desk() {
    translate([0, lift + slot_d * sin(tilt)]) rotate(-tilt) children();
}

// A screen-frame point in the desk frame
function to_desk_pt(p) =
    [p[0] * cos(tilt) + p[1] * sin(tilt),
     lift + slot_d * sin(tilt) - p[0] * sin(tilt) + p[1] * cos(tilt)];

// Where a line along the screen at screen-frame X meets the desk
function desk_x(X) = let(p = to_desk_pt([X, 0])) p[0] - p[1] * tan(tilt);

base_x0 = desk_x(-lip_t);
base_x1 = desk_x(slot_d + post_t) + base_rear;

module cradle_2d() {
    difference() {
        translate([-lip_t, -200]) square([lip_t + slot_d + post_t, 200 + post_h]);
        square([slot_d, 200]);                         // the screen
        translate([-lip_t - 1, lip_h]) square([lip_t + 1, 200]);  // above the lip
    }
}

// Back leg: full height where the screen's bottom meets the post, sloping
// down to the end of the base
module wedge_2d() {
    top = to_desk_pt([slot_d + post_t, 0]);
    polygon([top, [base_x1, base_t], [base_x1, 0], [desk_x(slot_d + post_t), 0]]);
}

module profile_2d() {
    intersection() {
        union() {
            to_desk() cradle_2d();
            translate([base_x0, 0]) square([base_x1 - base_x0, base_t]);
            wedge_2d();
        }
        translate([base_x0, 0]) square([base_x1 - base_x0, 300]);  // nothing below the desk
    }
}

// Outside corners rounded; inside corners (like the screen slot) stay sharp
module rounded_profile_2d() {
    offset(r = round_r) offset(delta = -round_r) profile_2d();
}

module stand() {
    difference() {
        minkowski() {
            translate([0, 0, edge_r])
                linear_extrude(stand_w - 2 * edge_r)
                    offset(delta = -edge_r) rounded_profile_2d();
            sphere(r = edge_r, $fn = 16);
        }
        for (y = stand_screw_y)
            to_desk() translate([slot_d - 1, y, stand_w / 2]) rotate([0, 90, 0]) {
                cylinder(d = hole_d, h = post_t + 2);
                translate([0, 0, 1 + post_t - head_depth])
                    cylinder(d = head_d, h = head_depth + 1);
            }
        for (x = [base_x0 + foot_inset, base_x1 - foot_inset])
            translate([x, -1, stand_w / 2]) rotate([-90, 0, 0])
                cylinder(d = foot_d, h = foot_depth + 1);
    }
}

assert(head_depth < post_t - 2, "head recess leaves too little post");
assert(max(stand_screw_y) + head_d / 2 < post_h, "screw above the top of the post");

stand();

echo(str("Slot ", slot_d, " mm for a ", device_d, " mm thick screen"));
