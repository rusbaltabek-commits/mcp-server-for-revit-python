---
name: photo-to-building
description: Reconstructs a building's exterior massing and facade (levels, walls, window grid, curtain-wall glazing, materials) in Revit from a photo of the building — as opposed to building-generator, which works from a top-down floor plan. Use when the user gives a photo/elevation image of an existing or reference building and wants it rebuilt in Revit ("собери это здание по фото", "построй такой же дом в Revit", "reconstruct this building from the photo"). Requires at least one real-world dimension to calibrate against — refuses to invent scale.
---

# Photo to Building

You turn a photo of a building's exterior (one or more facades) into real
Revit geometry: massing, window grid, curtain-wall glazing, and materials —
using this project's `generate_building`, `create_line_based_element`,
`set_curtain_wall_grid`, `set_curtain_wall_mullions`, `create_material`, and
`set_compound_layer_material` MCP tools (see
[revit-mcp-conventions](../skills/revit-mcp-conventions/SKILL.md) for how
each of those behaves — units, error-based type discovery, verification).

This is the **photo** counterpart to
[building-generator](building-generator.md), which handles top-down floor
plans instead. Use this one when the input is a photo/elevation/perspective
of a building's outside, not a plan drawing.

## 0. Calibration is mandatory — never invent a scale

A single photo carries no metric information. Before estimating any
dimension, get **at least one** real-world reference from the user:

- A stated floor-to-floor height, or total building height.
- A known door height (~2.1 m is a reasonable assumption *only if the user
  confirms it applies here* — don't silently assume).
- An overall width or depth.
- A named/addressable real building whose dimensions can be looked up.

If the user hasn't given one, ask before generating any geometry. If they
explicitly want a rough massing-only proxy with no real reference, generate
it, but label every dimension in your response as "estimated, not measured"
— don't present guessed numbers as fact. This mirrors the same rule already
enforced in `building-generator` for scale-less plans.

## 1. Read the photo and extract a building description

From the photo (or set of photos, if the user provides more than one
facade), estimate:

- **Floor count** and **floor-to-floor height**, scaled from the reference.
- **Footprint** — width of each visible facade. If only one or two facades
  are visible, say so explicitly and either ask for more photos/angles or
  extrude a flagged, estimated depth for the unseen sides.
- **Window grid per facade** — columns × rows, roughly even spacing; note
  any irregular bays (corners, entrances) separately rather than forcing a
  uniform grid where the photo shows one.
- **Ground-floor treatment** — storefront/curtain glazing vs. punched
  windows vs. solid base — these need different tool calls (curtain wall
  type vs. `openings` with a window family).
- **Facade zones and materials** — e.g. masonry base + upper glazing, or a
  single material throughout. Sample an approximate color per zone from the
  photo for `create_material`'s `color_rgb`.
- **Roof** — flat, since `generate_building`'s roof support is
  footprint-only with no pitch (see `revit-mcp-conventions`); if the photo
  shows a pitched roof, say the model will approximate it as flat and note
  the mismatch.

## 2. Discover real names in the target model first

Same principle as `building-generator` — match by exact name against what
exists, and don't guess:

- `list_levels`, `list_family_categories`, `list_families` (door/window
  families for punched openings).
- For curtain work, there is no dedicated "list curtain wall types" tool by
  design (see `revit-mcp-conventions`'s "try it, read the error"
  convention) — deliberately call `set_curtain_wall_grid` with a throwaway/
  unlikely `type_name` to harvest `available_curtain_wall_types` from the
  error, and similarly probe `set_curtain_wall_mullions` for
  `available_mullion_types`.
- If no curtain-wall-kind type exists in the model yet and the user's Revit
  install has none loadable either, say so — a new curtain wall system
  family/type can't be authored through this project's tools (family
  authoring isn't exposed; see `revit-mcp-conventions`), only an existing
  type's grid/mullions can be adjusted.

## 3. Build

1. **Levels and shell**: one `generate_building` call for `levels`, `floors`
   (per level), and any solid/masonry `walls` with punched-window
   `openings`.
2. **Glazing facades**: create curtain-wall segments as additional walls
   (via `create_line_based_element` or another `generate_building` pass)
   using a curtain-kind `type_name`, positioned/sized to match the
   photographed glazing zone (e.g. full-height storefront at ground floor).
3. **Match the grid**: `set_curtain_wall_grid` per curtain segment's type —
   `fixed_number` when the photo shows a clear bay count, `fixed_distance`
   when a spacing reads more consistently.
4. **Mullions**: `set_curtain_wall_mullions` for a visible frame profile:
   `position="interior"` first (the one most likely to resolve — see the
   known caveat below), then `border1`/`border2` if the photo shows a
   distinct edge frame.
5. **Materials — iterate against the photo, don't guess once and stop**:
   `create_material` per facade zone/color sampled from the photo, then
   `set_compound_layer_material` on the relevant wall type (`layer_index=0`
   for the exterior-facing layer, per `revit-mcp-conventions`'s default).
   Pull a render with `get_revit_view` after the first pass and compare it
   against the photo specifically for color/material — not just geometry.
   If it's clearly off, adjust the material's `color_rgb` (or swap to a
   better-matching existing material) and re-render. Two or three rounds is
   normal here — the same eyeball-and-correct loop a person does picking a
   paint color, not a single API call. Report honestly if it still doesn't
   converge, rather than claiming a match that isn't there.

## 4. Final verification against the photo

After the per-zone material iteration above has already converged (or
you've reported where it didn't), do one holistic pass: pull a render with
`get_revit_view` and compare it side-by-side against the source photo in
your response — call out concretely where it diverges (window count, floor
count, proportions, color) instead of declaring a match. Re-query with
`get_current_view_elements` to confirm counts (walls, curtain panels) match
what was intended.

## Known caveat: mullion parameter resolution

`set_curtain_wall_mullions`/`set_curtain_wall_grid` resolve their target
Revit parameter against a candidate list (see `revit-mcp-conventions` and
the docstring in `revit_mcp/curtain_walls.py`) because Revit stores the
vertical/horizontal versions of these fields under separate
`BuiltInParameter`s that share a display name, and this wasn't verified
against a live Revit session while the tool was written. If a call returns
an error naming what it tried rather than succeeding, that's the tool being
honest about an unresolved parameter — report it to the user rather than
retrying blindly, and suggest the one-time manual Edit Type step the error
message names.

## What this agent does not do

- No true photogrammetry or multi-view 3D reconstruction — this is a
  single/few-photo visual approximation, not a survey.
- No pitched roofs, stairs, columns, topography, or site context — see
  `revit-mcp-conventions`'s "Known gaps."
- No code-compliance design (areas, zoning, egress).
