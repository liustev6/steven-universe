// Shared frame dimensions for screen_wrapper.scad and back_cover.scad.
// Change sizes here so the wrapper and the back cover stay aligned.

screen_w  = 165;   // screen module width (+2 mm after first test fit was too narrow)
screen_h  = 110;   // screen module height
fit       = 0.4;   // clearance per side around the screen
wall      = 7;     // top/bottom wall thickness (needs room for the inserts)
side_wall = 6;     // left/right walls, thinner so it fits the 180 mm A1 mini bed
corner_r  = 4;     // outer corner radius

pocket_w = screen_w + 2 * fit;
pocket_h = screen_h + 2 * fit;
outer_w  = pocket_w + 2 * side_wall;
outer_h  = pocket_h + 2 * wall;

// Centers of the 4 corner M3 holes, with the frame centered on the origin
corner_holes = [for (sx = [-1, 1], sy = [-1, 1])
    [sx * (pocket_w + side_wall) / 2, sy * (pocket_h + wall) / 2]];

// Desk stands (stand.scad) screw into the back cover here, as seen from
// the back. The left stand sits 1/3 of the way from the left screw (13.5)
// to the island (46), then 3 mm further in; the right one mirrors it.
stand_left_x  = 13.5 + (46 - 13.5) / 3 + 3;
stand_x       = [stand_left_x, outer_w - stand_left_x];  // from the cover's left edge
stand_screw_y = [12, 35];        // screw heights, from the cover's bottom edge

module rounded_rect(w, h, r) {
    offset(r) square([w - 2 * r, h - 2 * r], center = true);
}
