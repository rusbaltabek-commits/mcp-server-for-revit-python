---
name: revit-mcp-conventions
description: Working conventions for driving a live Revit model through THIS project's own MCP tools (generate_building, create_line_based_element, create_surface_based_element, place_family, modify_element, delete_elements, color_splash, execute_revit_code, etc.) once they're already built. Use this before actually modeling something through the MCP client — as opposed to add-revit-mcp-tool, which is for building brand-new tools. Covers units, type-name discovery, batching, verification, and current gaps in tool coverage.
---

# Revit MCP — Working Conventions (using this project's own tools)

Ground rules for driving a live Revit model through this project's existing
MCP tools. For adding a *new* tool, use
[add-revit-mcp-tool](../add-revit-mcp-tool/SKILL.md) instead — this skill is
about using the tools that already exist to actually build or edit something.

## Units: meters everywhere, in both directions

Every tool that takes or returns a length, coordinate, elevation, or height
speaks **meters**, not Revit's native feet — the conversion happens inside
`revit_mcp/*.py` (`feet_to_meters` / `meters_to_feet`) before the value ever
crosses HTTP. Concretely:

- `create_line_based_element`, `create_surface_based_element`,
  `generate_building`, `place_family`: pass coordinates, heights, and
  elevations in meters as-is.
- `list_levels`, `get_current_view_elements` (with `include_location=True`),
  `get_selected_elements`: read back in meters.

Don't convert feet↔meters yourself before calling these — that double-converts.
**Exception:** `execute_revit_code` drops straight into the Revit API, which
is internally in **feet**. Convert explicitly (`* 0.3048` / `/ 0.3048`, or use
`DB.UnitUtils`) whenever that escape hatch touches lengths.

## Don't pre-query type names — try it, then read the error

Unlike a "always inspect before you build" workflow, the creation tools here
already do that inspection for you: pass whatever `type_name` you have (or
omit it to get the model's first available type), and if it doesn't match
exactly, the error response itself lists the valid options:

- `create_line_based_element` (walls) → `available_wall_types`
- `create_surface_based_element` (floors) → `available_floor_types`
- `generate_building` roofs → `available_roof_types`
- `place_family` → `available_families`

Retry once with a name from that list rather than guessing or hunting for a
separate "list types" tool — there isn't one, by design. Matching is an
**exact string match** against the type's display name, not fuzzy or
substring (that's what `list_families(contains=...)` is for, on the family
side, when you genuinely don't know the name yet).

## Prefer `generate_building` over one-element-at-a-time calls

For anything beyond a couple of elements, use `generate_building` rather than
looping `create_line_based_element` / `create_surface_based_element` /
`place_family` calls:

- It runs as **one Revit transaction** for the whole payload.
- A bad item (missing level, unknown type, degenerate geometry) is skipped
  and reported back in `*_failed` with a reason — it does **not** abort the
  rest of the building. Read `walls_failed`, `floors_failed`,
  `roofs_failed`, etc. in the response and report gaps back to the user
  instead of assuming success because the call returned.
- Walls carry a caller-defined `"id"` so `openings` can host doors/windows on
  them by reference — plan wall IDs up front if the building needs openings.
- Rooms only get created if `"point"` falls inside a fully closed loop of
  walls at that level; an open loop silently skips that room (it shows up in
  `rooms_failed`, not a hard error).

## Roofs are flat footprint roofs only

`generate_building`'s roof support wraps `NewFootPrintRoof` directly with no
slope/pitch parameters — every roof it creates is flat, at the given level,
over the given boundary. There is no gable/hip pitch control yet. If the user
needs a sloped roof, say so explicitly rather than silently producing a flat
one, and fall back to `execute_revit_code` (see below) or flag it as a gap.

One thing this project does **not** have to worry about that similar
Revit-AI connectors do: the roof route calls `doc.Create.NewFootPrintRoof`
directly through the API, with no dependency on which view is active in the
UI. (Contrast with connectors that fail on `create_roof` unless a floor plan
view happens to be active — that's a UI-context bug in *their* implementation,
not a general Revit API constraint, and it doesn't apply here.)

## Verification: look before declaring success

Don't trust a `"status": "success"` alone for anything non-trivial:

- `get_current_view_elements` (with `include_location=True` if you need to
  check placement) to re-query counts and confirm what actually landed in
  the model.
- `get_revit_view(view_name)` to pull a rendered image of a view for a visual
  sanity check — the closest equivalent to "export and eyeball it."
- For per-element edits, `get_selected_elements` after asking the user to
  select, or re-run `modify_element`'s target query, to confirm the
  parameter actually took (some parameters are read-only or type-driven and
  silently fail to apply at the instance level).

## Known gaps — say so, don't fake it

As of this writing, these categories have **no dedicated tool** — there is no
massing (box/extrusion/blend), topography/site, stairs, column, or grid
tool. `create_line_based_element` only supports `category="wall"` and
`create_surface_based_element` only supports `category="floor"`, despite the
parameter existing for future categories. Check `README.md`'s tool status
table before assuming something exists.

**Materials and curtain-wall facades are covered** (`create_material`,
`set_compound_layer_material`, `set_curtain_wall_grid`,
`set_curtain_wall_mullions`) — curtain walls themselves are still created via
the normal wall pipeline (`create_line_based_element`/`generate_building`'s
`walls`, with `type_name` naming a curtain-kind wall type); these two new
tools only add bay-spacing and mullion control on top. One caveat specific
to these: the exact `BuiltInParameter` names behind the mullion/grid-layout
fields were written without a live Revit session to verify against (Revit
wasn't running at the time), so `revit_mcp/curtain_walls.py` resolves them
against a candidate list with a documented fallback rather than one hardcoded
guess. Check the `resolved_via` field in a real response the first time this
runs — if it says "display name + group match" instead of a clean
`BuiltInParameter.X`, the primary candidate list guessed wrong and could use
tightening.

When a task needs one of these:

1. Say plainly that it's not covered by a dedicated tool yet.
2. Offer `execute_revit_code` as the fallback — it has full Revit API access
   (`doc`, `uidoc`, `DB`, `revit`), but remember: no automatic transaction
   (wrap mutations in `DB.Transaction` yourself), and it works in **feet**,
   not meters.
3. If the gap looks reusable rather than one-off, mention it's a candidate
   for a real tool — see
   [add-revit-mcp-tool](../add-revit-mcp-tool/SKILL.md) and README's
   `🔄 Pending` rows.

## Non-ASCII names just work — you don't need to route around them

Level, room, family, and parameter names in Cyrillic (this project's Revit
installs are often Russian-localized) go through the mojibake fixes already
built into `revit_mcp/utils.py` on both the way in and out. Pass Cyrillic
strings straight through (e.g. `generate_building`'s room `"name": "Гостиная"`)
— don't pre-encode, escape, or transliterate them defensively.
