---
name: building-generator
description: Generates a whole building (levels, walls, doors/windows, floors, roofs, rooms) in Revit from a floor-plan image/PDF/sketch or a verbal description, by writing one structured JSON payload and calling the generate_building MCP tool, which builds everything inside Revit in a single transaction. Use when the user wants to turn a house/building plan into a real Revit model — "сгенерируй здание", "собери дом по плану", "построй дом/здание в Revit по картинке" — rather than a single family/component.
---

# Building Generator

You turn a floor plan (image, PDF, sketch, or verbal description) into real
Revit geometry, in one pass, using the `generate_building` MCP tool from the
mcp-server-for-revit-python project ([tools/building_tools.py](../../tools/building_tools.py) /
[revit_mcp/building.py](../../revit_mcp/building.py)). When the reference
also shows real colors/materials (a roof color, a cladding finish), also use
`create_material` and `set_compound_layer_material` (step 5 below) — see
[revit-mcp-conventions](../skills/revit-mcp-conventions/SKILL.md) for how
every tool in this project behaves (units, error-based type discovery,
verification).

## JSON schema (all lengths/coordinates in meters)

```json
{
  "levels": [{"name": "Level 1", "elevation": 0.0}],
  "walls": [
    {"id": "W1", "level": "Level 1",
     "start": {"x": 0.0, "y": 0.0}, "end": {"x": 6.0, "y": 0.0},
     "height": 3.0, "type_name": "Generic - 200mm"}
  ],
  "openings": [
    {"host_wall": "W1", "kind": "door", "distance_along_wall": 1.5,
     "family_name": "Single-Flush", "type_name": "0915 x 2134mm",
     "sill_height": 0.0}
  ],
  "floors": [
    {"level": "Level 1", "type_name": "Generic 150mm",
     "boundary": [{"x":0,"y":0}, {"x":6,"y":0}, {"x":6,"y":4}, {"x":0,"y":4}]}
  ],
  "roofs": [
    {"level": "Level 2", "type_name": "Generic - 400mm",
     "boundary": [{"x":0,"y":0}, {"x":6,"y":0}, {"x":6,"y":4}, {"x":0,"y":4}]}
  ],
  "rooms": [
    {"level": "Level 1", "name": "Living Room", "point": {"x": 3.0, "y": 2.0}}
  ]
}
```

- `walls[].id` is caller-defined, used only to host `openings` on that wall.
- `walls[].height`/`type_name` are optional: height defaults to the gap to
  the next level up (else 3m), type defaults to the first available WallType.
- `openings[].distance_along_wall` is measured from the wall's **start**
  point, not its center.
- `rooms[].point` must land inside a closed loop of walls on that level, or
  `NewRoom` creates nothing and the entry reports in `rooms_failed`.
- `roofs` are always flat footprint roofs — no slope. For a pitched roof,
  generate the flat footprint, then set edge slopes manually in Revit.

## Workflow

1. **Read the plan and extract geometry.** Wall centerlines as `start`→`end`
   segments, opening positions along walls, room names/points, level count.
   If the plan has no scale, look for a dimension stamp or calibrate against
   a known room area from the schedule — never eyeball it. A closed wall
   loop matters twice: for `rooms` (or `NewRoom` misses) and for
   floors/roofs (an open loop gives a degenerate `CurveLoop`, which fails).

2. **Discover what actually exists in the target model — before writing
   names, not after a failed attempt.** `type_name`/`family_name` match by
   exact name against what's already loaded; a mismatch doesn't fail the
   whole request, it just drops that one wall/opening/floor/roof into
   `*_failed`. Call first:
   - `mcp__Revit_Connector__list_levels` — exact level names
   - `mcp__Revit_Connector__list_family_categories` — what categories exist
   - `mcp__Revit_Connector__list_families` (contains="Door"/"Window", or the
     Russian equivalents "Дверь"/"Окно" for RU-localized models) — real
     family_name/type_name values
   If wall/floor/roof type names are unknown, omit `type_name` — the first
   available type of that category is used, which is fine for a massing pass.

3. **Assemble one JSON for the whole building and call `generate_building`
   once** — every level, every wall on every floor, every opening, floor,
   roof, room, in a single call. Not element-by-element.

4. **Read the response.** It carries `*_created` and `*_failed` per
   category — report `*_failed` reasons back to the user (missing level,
   missing family/type, room point outside the wall loop). A partial result
   is a normal outcome, not a failure of the call.

5. **Match visible materials/finishes, if the reference shows them — and
   verify by eye, not just by geometry.** Elevation drawings and photos
   often carry real information beyond the outline: a roof color, a
   cladding material, a trim color. When the reference includes this:
   - `create_material` per distinct color/material zone (roof, walls,
     trim), sampling an approximate `color_rgb` from the reference image.
   - `set_compound_layer_material` on the matching wall/floor/roof
     `type_name` (`layer_index=0` for the exterior-facing layer by
     default).
   - **Iterate, don't guess once and stop**: pull a render with
     `get_revit_view`, compare it side-by-side against the reference, and
     if the color/material clearly doesn't match, adjust and re-render.
     Two or three rounds is normal — this is the same eyeball-and-correct
     loop a person does picking a color, not a one-shot API call. If it
     still doesn't converge after a few tries, say so plainly rather than
     settling silently or claiming a match that isn't there.
   - This is best-effort visual matching from a raster image, not a real
     material/finish specification — say so if the user needs an exact
     spec (manufacturer, finish code) rather than a visual match.

## Hard constraints

- **Re-running does not deduplicate.** Levels are matched by name and
  reused, but walls/floors/roofs/rooms are created fresh on every call —
  calling `generate_building` again with the same JSON duplicates the whole
  building. To fix part of an already-built model, use `modify_element` /
  `delete_elements` on specific elements, not a full re-run.
- Verify in Revit, not just by response status. `RoofType`/`NewFootPrintRoof`
  behavior and the `Sill Height` parameter on specific window families can't
  be checked outside a live Revit session — build a test house and look at
  it. Check Cyrillic names (rooms/levels) specifically — that's where
  encoding bugs have historically hidden (see `fix_mojibake_deep` /
  `to_param_string` in `revit_mcp/utils.py`).

## What this agent does not do

- No code-compliance design (areas, zoning, egress) — that's a separate
  concern; for healthcare facilities in Kazakhstan see the
  `poliklinika-zoning-check` skill.
- No pitched roofs, stairs, or MEP — shell/layout geometry plus optional
  best-effort exterior color/material matching (step 5), nothing deeper.
- No raster-scan digitization beyond ordinary image reading — for precise
  vector-PDF takeoffs see the `poliklinika-room-modules` skill's
  `extract_room_dims.py`.
