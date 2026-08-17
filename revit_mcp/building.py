# -*- coding: UTF-8 -*-
"""
Building Generator Module for Revit MCP
Builds a whole building (levels, walls, doors/windows, floors, roofs, rooms)
from a single structured JSON payload, in one transaction.
"""

from pyrevit import routes, DB
import json
import logging

from utils import (
    normalize_string,
    get_element_name,
    element_id_value,
    meters_to_feet,
    to_param_string,
    fix_mojibake_deep,
    find_family_symbol_safely,
)

logger = logging.getLogger(__name__)


def _find_level_by_name(doc, level_name):
    levels = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Levels)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    target = normalize_string(level_name).strip().lower()
    for level in levels:
        try:
            if normalize_string(get_element_name(level)).strip().lower() == target:
                return level
        except Exception:
            continue
    return None


def _sorted_levels(doc):
    levels = list(
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Levels)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    levels.sort(key=lambda lvl: lvl.Elevation)
    return levels


def _default_wall_height_ft(doc, level):
    """Level-to-level height if a level above exists, else a 3m fallback."""
    for lvl in _sorted_levels(doc):
        if lvl.Elevation > level.Elevation + 0.001:
            return lvl.Elevation - level.Elevation
    return meters_to_feet(3.0)


def _get_or_create_level(doc, level_name, elevation_m):
    level = _find_level_by_name(doc, level_name)
    if level:
        return level, False
    elevation_ft = meters_to_feet(float(elevation_m or 0.0))
    new_level = DB.Level.Create(doc, elevation_ft)
    new_level.Name = to_param_string(level_name)
    return new_level, True


def _find_type_by_name(doc, type_class, type_name):
    """Find a type element by display name, tolerant of missing type_name
    (falls back to the first available type). Returns (type_elem, available)."""
    types = list(DB.FilteredElementCollector(doc).OfClass(type_class).ToElements())
    if type_name:
        target = normalize_string(type_name).strip().lower()
        for t in types:
            try:
                if normalize_string(get_element_name(t)).strip().lower() == target:
                    return t, types
            except Exception:
                continue
        return None, types
    return (types[0] if types else None), types


def _available_names(types, limit=20):
    return sorted(
        set(normalize_string(get_element_name(t)) for t in types)
    )[:limit]


def _xyz(point_m, z_ft=0.0):
    return DB.XYZ(
        meters_to_feet(float(point_m.get("x", 0.0))),
        meters_to_feet(float(point_m.get("y", 0.0))),
        z_ft,
    )


def register_building_routes(api):
    """Register the whole-building generator route with the API"""

    @api.route("/generate_building/", methods=["POST"])
    def generate_building(doc, request):
        """
        Build a whole building from a single JSON description: levels,
        walls, doors/windows, floors, roofs and rooms, all created in one
        transaction. Coordinates, lengths and elevations are in meters.

        Expected request data:
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
                {"level": "Level 1", "name": "Гостиная", "point": {"x": 3.0, "y": 2.0}}
            ]
        }

        Individual items that fail (missing level, missing family, bad
        geometry, ...) are skipped and reported in the matching "*_failed"
        list rather than aborting the whole building; only unexpected
        top-level errors roll back the full transaction.
        """
        try:
            if not doc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )
            if not request or not request.data:
                return routes.make_response(
                    data={"error": "No data provided"}, status=400
                )

            data = (
                json.loads(request.data)
                if isinstance(request.data, str)
                else request.data
            )
            if not isinstance(data, dict):
                return routes.make_response(
                    data={"error": "Invalid data format - expected JSON object"},
                    status=400,
                )
            data = fix_mojibake_deep(data)

            levels_in = data.get("levels", [])
            walls_in = data.get("walls", [])
            openings_in = data.get("openings", [])
            floors_in = data.get("floors", [])
            roofs_in = data.get("roofs", [])
            rooms_in = data.get("rooms", [])

            levels_created = []
            levels_reused = []
            walls_created = []
            walls_failed = []
            openings_created = []
            openings_failed = []
            floors_created = []
            floors_failed = []
            roofs_created = []
            roofs_failed = []
            rooms_created = []
            rooms_failed = []

            t = DB.Transaction(doc, "Generate Building via MCP")
            t.Start()

            try:
                # --- Levels ---------------------------------------------------
                for lvl_def in levels_in:
                    name = lvl_def.get("name")
                    if not name:
                        continue
                    try:
                        level, created = _get_or_create_level(
                            doc, name, lvl_def.get("elevation", 0.0)
                        )
                        if created:
                            levels_created.append(name)
                        else:
                            levels_reused.append(name)
                    except Exception as e:
                        logger.error("Failed to create level {}: {}".format(name, str(e)))

                doc.Regenerate()

                # --- Walls ------------------------------------------------------
                walls_by_id = {}
                for i, wall_def in enumerate(walls_in):
                    wall_id = wall_def.get("id") or "wall_{}".format(i)
                    try:
                        level_name = wall_def.get("level")
                        level = _find_level_by_name(doc, level_name)
                        if not level:
                            walls_failed.append(
                                {"id": wall_id, "reason": "Level not found: {}".format(level_name)}
                            )
                            continue

                        start = wall_def.get("start", {})
                        end = wall_def.get("end", {})
                        if not all(k in start for k in ["x", "y"]) or not all(
                            k in end for k in ["x", "y"]
                        ):
                            walls_failed.append(
                                {"id": wall_id, "reason": "start and end must each include x and y"}
                            )
                            continue

                        wall_type, available = _find_type_by_name(
                            doc, DB.WallType, wall_def.get("type_name")
                        )
                        if not wall_type:
                            walls_failed.append(
                                {
                                    "id": wall_id,
                                    "reason": "Wall type not found: {}".format(
                                        wall_def.get("type_name")
                                    ),
                                    "available_wall_types": _available_names(available),
                                }
                            )
                            continue

                        start_pt = _xyz(start)
                        end_pt = _xyz(end)
                        if start_pt.DistanceTo(end_pt) < 0.01:
                            walls_failed.append(
                                {"id": wall_id, "reason": "start and end points are too close together"}
                            )
                            continue

                        height = wall_def.get("height")
                        wall_height_ft = (
                            meters_to_feet(float(height))
                            if height
                            else _default_wall_height_ft(doc, level)
                        )

                        curve = DB.Line.CreateBound(start_pt, end_pt)
                        new_wall = DB.Wall.Create(
                            doc, curve, wall_type.Id, level.Id, wall_height_ft, 0.0, False, False
                        )

                        walls_by_id[wall_id] = {
                            "wall": new_wall,
                            "start": start_pt,
                            "end": end_pt,
                            "level": level,
                        }
                        walls_created.append(
                            {"id": wall_id, "element_id": element_id_value(new_wall.Id)}
                        )
                    except Exception as e:
                        walls_failed.append({"id": wall_id, "reason": str(e)})
                        logger.error("Failed to create wall {}: {}".format(wall_id, str(e)))

                doc.Regenerate()

                # --- Openings (doors/windows) -----------------------------------
                for i, op_def in enumerate(openings_in):
                    op_label = op_def.get("host_wall", "opening_{}".format(i))
                    try:
                        host_info = walls_by_id.get(op_def.get("host_wall"))
                        if not host_info:
                            openings_failed.append(
                                {"host_wall": op_label, "reason": "host_wall not found among created walls"}
                            )
                            continue

                        family_name = op_def.get("family_name")
                        type_name = op_def.get("type_name")
                        symbol = find_family_symbol_safely(doc, family_name, type_name)
                        if not symbol:
                            openings_failed.append(
                                {
                                    "host_wall": op_label,
                                    "reason": "Family/type not found: {} / {}".format(
                                        family_name, type_name
                                    ),
                                }
                            )
                            continue

                        if not symbol.IsActive:
                            symbol.Activate()
                            doc.Regenerate()

                        start_pt = host_info["start"]
                        end_pt = host_info["end"]
                        wall = host_info["wall"]
                        level = host_info["level"]
                        direction = (end_pt - start_pt).Normalize()
                        distance_ft = meters_to_feet(
                            float(op_def.get("distance_along_wall", 0.0))
                        )
                        location_pt = DB.XYZ(
                            start_pt.X + direction.X * distance_ft,
                            start_pt.Y + direction.Y * distance_ft,
                            level.Elevation,
                        )

                        new_instance = doc.Create.NewFamilyInstance(
                            location_pt,
                            symbol,
                            wall,
                            level,
                            DB.Structure.StructuralType.NonStructural,
                        )

                        kind = normalize_string(op_def.get("kind", "door")).lower()
                        if kind == "window" and op_def.get("sill_height") is not None:
                            try:
                                sill_param = new_instance.LookupParameter("Sill Height")
                                if not sill_param:
                                    sill_param = new_instance.get_Parameter(
                                        getattr(DB.BuiltInParameter, "INSTANCE_SILL_HEIGHT_PARAM")
                                    )
                                if sill_param and not sill_param.IsReadOnly:
                                    sill_param.Set(
                                        meters_to_feet(float(op_def.get("sill_height")))
                                    )
                            except Exception:
                                pass

                        openings_created.append(
                            {
                                "host_wall": op_label,
                                "kind": kind,
                                "element_id": element_id_value(new_instance.Id),
                            }
                        )
                    except Exception as e:
                        openings_failed.append({"host_wall": op_label, "reason": str(e)})
                        logger.error("Failed to create opening on {}: {}".format(op_label, str(e)))

                doc.Regenerate()

                # --- Floors -------------------------------------------------------
                for i, floor_def in enumerate(floors_in):
                    label = "floor_{}".format(i)
                    try:
                        level_name = floor_def.get("level")
                        level = _find_level_by_name(doc, level_name)
                        if not level:
                            floors_failed.append(
                                {"index": i, "reason": "Level not found: {}".format(level_name)}
                            )
                            continue

                        boundary = floor_def.get("boundary", [])
                        if not boundary or len(boundary) < 3:
                            floors_failed.append(
                                {"index": i, "reason": "boundary must contain at least 3 points"}
                            )
                            continue

                        floor_type, available = _find_type_by_name(
                            doc, DB.FloorType, floor_def.get("type_name")
                        )
                        if not floor_type:
                            floors_failed.append(
                                {
                                    "index": i,
                                    "reason": "Floor type not found: {}".format(
                                        floor_def.get("type_name")
                                    ),
                                    "available_floor_types": _available_names(available),
                                }
                            )
                            continue

                        points = [_xyz(pt) for pt in boundary]
                        curve_loop = DB.CurveLoop()
                        for j in range(len(points)):
                            curve_loop.Append(
                                DB.Line.CreateBound(points[j], points[(j + 1) % len(points)])
                            )

                        try:
                            from System.Collections.Generic import List

                            curve_loops = List[DB.CurveLoop]()
                            curve_loops.Add(curve_loop)
                            new_floor = DB.Floor.Create(doc, curve_loops, floor_type.Id, level.Id)
                        except AttributeError:
                            curve_array = DB.CurveArray()
                            for curve in curve_loop:
                                curve_array.Append(curve)
                            new_floor = doc.Create.NewFloor(curve_array, floor_type, level, False)

                        floors_created.append(
                            {"index": i, "element_id": element_id_value(new_floor.Id)}
                        )
                    except Exception as e:
                        floors_failed.append({"index": i, "reason": str(e)})
                        logger.error("Failed to create floor {}: {}".format(i, str(e)))

                # --- Roofs --------------------------------------------------------
                for i, roof_def in enumerate(roofs_in):
                    try:
                        level_name = roof_def.get("level")
                        level = _find_level_by_name(doc, level_name)
                        if not level:
                            roofs_failed.append(
                                {"index": i, "reason": "Level not found: {}".format(level_name)}
                            )
                            continue

                        boundary = roof_def.get("boundary", [])
                        if not boundary or len(boundary) < 3:
                            roofs_failed.append(
                                {"index": i, "reason": "boundary must contain at least 3 points"}
                            )
                            continue

                        roof_type, available = _find_type_by_name(
                            doc, DB.RoofType, roof_def.get("type_name")
                        )
                        if not roof_type:
                            roofs_failed.append(
                                {
                                    "index": i,
                                    "reason": "Roof type not found: {}".format(
                                        roof_def.get("type_name")
                                    ),
                                    "available_roof_types": _available_names(available),
                                }
                            )
                            continue

                        points = [_xyz(pt) for pt in boundary]
                        curve_array = DB.CurveArray()
                        for j in range(len(points)):
                            curve_array.Append(
                                DB.Line.CreateBound(points[j], points[(j + 1) % len(points)])
                            )

                        footprint_map = DB.ModelCurveArray()
                        result = doc.Create.NewFootPrintRoof(
                            curve_array, level, roof_type, footprint_map
                        )
                        new_roof = result[0] if isinstance(result, tuple) else result

                        roofs_created.append(
                            {"index": i, "element_id": element_id_value(new_roof.Id)}
                        )
                    except Exception as e:
                        roofs_failed.append({"index": i, "reason": str(e)})
                        logger.error("Failed to create roof {}: {}".format(i, str(e)))

                doc.Regenerate()

                # --- Rooms ----------------------------------------------------------
                for i, room_def in enumerate(rooms_in):
                    name = room_def.get("name", "room_{}".format(i))
                    try:
                        level_name = room_def.get("level")
                        level = _find_level_by_name(doc, level_name)
                        if not level:
                            rooms_failed.append(
                                {"name": name, "reason": "Level not found: {}".format(level_name)}
                            )
                            continue

                        point = room_def.get("point", {})
                        if not all(k in point for k in ["x", "y"]):
                            rooms_failed.append(
                                {"name": name, "reason": "point must include x and y"}
                            )
                            continue

                        uv = DB.UV(
                            meters_to_feet(float(point["x"])),
                            meters_to_feet(float(point["y"])),
                        )
                        new_room = doc.Create.NewRoom(level, uv)
                        if not new_room:
                            rooms_failed.append(
                                {"name": name, "reason": "NewRoom returned nothing - point may not be enclosed by walls"}
                            )
                            continue

                        try:
                            new_room.Name = to_param_string(name)
                        except Exception:
                            pass

                        rooms_created.append(
                            {"name": name, "element_id": element_id_value(new_room.Id)}
                        )
                    except Exception as e:
                        rooms_failed.append({"name": name, "reason": str(e)})
                        logger.error("Failed to create room {}: {}".format(name, str(e)))

                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "levels_created": levels_created,
                        "levels_reused": levels_reused,
                        "walls_created": walls_created,
                        "walls_failed": walls_failed,
                        "openings_created": openings_created,
                        "openings_failed": openings_failed,
                        "floors_created": floors_created,
                        "floors_failed": floors_failed,
                        "roofs_created": roofs_created,
                        "roofs_failed": roofs_failed,
                        "rooms_created": rooms_created,
                        "rooms_failed": rooms_failed,
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to generate building: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to generate building: {}".format(str(e))},
                status=500,
            )

    logger.info("Building generator routes registered successfully")
