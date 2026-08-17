# -*- coding: UTF-8 -*-
"""
Creation Module for Revit MCP
Handles creation of line-based (walls) and surface-based (floors) elements
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
)

logger = logging.getLogger(__name__)


def _find_level_by_name(doc, level_name):
    levels = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_Levels)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    for level in levels:
        try:
            if get_element_name(level) == level_name:
                return level
        except Exception:
            continue
    return None


def _apply_properties(elem, properties):
    """Set instance parameters on a newly created element, returning (set, failed)."""
    properties_set = []
    properties_failed = []
    for param_name, param_value in properties.items():
        try:
            param = elem.LookupParameter(param_name)
            if not param or param.IsReadOnly:
                properties_failed.append("{} (read-only or not found)".format(param_name))
                continue

            if param.StorageType == DB.StorageType.String:
                param.Set(to_param_string(param_value))
            elif param.StorageType == DB.StorageType.Integer:
                param.Set(int(param_value))
            elif param.StorageType == DB.StorageType.Double:
                param.Set(float(param_value))
            else:
                properties_failed.append("{} (unsupported type)".format(param_name))
                continue

            properties_set.append(param_name)
        except Exception as param_error:
            properties_failed.append("{} (error: {})".format(param_name, str(param_error)))

    return properties_set, properties_failed


def register_creation_routes(api):
    """Register element creation routes with the API"""

    @api.route("/create_line_based_element/", methods=["POST"])
    def create_line_based_element(doc, request):
        """
        Create a line-based element between two points. Currently only the
        'wall' category is supported.

        Expected request data:
        {
            "category": "wall",
            "type_name": "Generic - 200mm",
            "level_name": "Level 1",
            "start": {"x": 0.0, "y": 0.0},
            "end": {"x": 5.0, "y": 0.0},
            "height": 3.0,
            "properties": {"Comments": "Created via MCP"}
        }

        Coordinates and height are in meters. "type_name" and "height" are
        optional - the model's default wall type and level-appropriate
        height are used when omitted.
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

            category = normalize_string(data.get("category", "wall")).lower()
            if category != "wall":
                return routes.make_response(
                    data={
                        "error": "Unsupported category '{}'. Only 'wall' is currently supported.".format(
                            category
                        )
                    },
                    status=400,
                )

            type_name = data.get("type_name")
            level_name = data.get("level_name")
            start = data.get("start", {})
            end = data.get("end", {})
            height = data.get("height")
            properties = data.get("properties", {})

            if not level_name:
                return routes.make_response(
                    data={"error": "No level_name provided"}, status=400
                )
            if not all(k in start for k in ["x", "y"]) or not all(
                k in end for k in ["x", "y"]
            ):
                return routes.make_response(
                    data={
                        "error": "start and end must each include x and y coordinates"
                    },
                    status=400,
                )

            level = _find_level_by_name(doc, level_name)
            if not level:
                return routes.make_response(
                    data={"error": "Level not found: {}".format(level_name)},
                    status=404,
                )

            wall_types = (
                DB.FilteredElementCollector(doc).OfClass(DB.WallType).ToElements()
            )
            wall_type = None
            if type_name:
                for wt in wall_types:
                    if get_element_name(wt) == type_name:
                        wall_type = wt
                        break
                if not wall_type:
                    available = sorted(
                        [normalize_string(get_element_name(wt)) for wt in wall_types]
                    )
                    return routes.make_response(
                        data={
                            "error": "Wall type not found: {}".format(type_name),
                            "available_wall_types": available[:20],
                        },
                        status=404,
                    )
            else:
                wall_type = wall_types[0] if wall_types else None
                if not wall_type:
                    return routes.make_response(
                        data={"error": "No wall types available in the model"},
                        status=404,
                    )

            start_pt = DB.XYZ(
                meters_to_feet(float(start["x"])), meters_to_feet(float(start["y"])), 0
            )
            end_pt = DB.XYZ(
                meters_to_feet(float(end["x"])), meters_to_feet(float(end["y"])), 0
            )

            if start_pt.DistanceTo(end_pt) < 0.01:
                return routes.make_response(
                    data={
                        "error": "start and end points are too close together to form a wall"
                    },
                    status=400,
                )

            curve = DB.Line.CreateBound(start_pt, end_pt)
            # Default to a generous 3m (~10ft) wall height when not specified
            wall_height = meters_to_feet(float(height)) if height else 10.0

            t = DB.Transaction(doc, "Create Wall via MCP")
            t.Start()

            try:
                new_wall = DB.Wall.Create(
                    doc, curve, wall_type.Id, level.Id, wall_height, 0.0, False, False
                )

                properties_set, properties_failed = _apply_properties(
                    new_wall, properties
                )

                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "element_id": element_id_value(new_wall.Id),
                        "category": "wall",
                        "type_name": normalize_string(get_element_name(wall_type)),
                        "level": level_name,
                        "properties_set": properties_set,
                        "properties_failed": properties_failed,
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to create line-based element: {}".format(str(e)))
            return routes.make_response(
                data={
                    "error": "Failed to create line-based element: {}".format(str(e))
                },
                status=500,
            )

    @api.route("/create_surface_based_element/", methods=["POST"])
    def create_surface_based_element(doc, request):
        """
        Create a surface-based element from a closed boundary loop. Currently
        only the 'floor' category is supported.

        Expected request data:
        {
            "category": "floor",
            "type_name": "Generic 150mm",
            "level_name": "Level 1",
            "boundary": [
                {"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0},
                {"x": 5.0, "y": 5.0}, {"x": 0.0, "y": 5.0}
            ],
            "properties": {"Comments": "Created via MCP"}
        }

        Coordinates are in meters. "boundary" must contain at least 3 points,
        given in order (the loop is closed automatically). "type_name" is
        optional - the model's default floor type is used when omitted.
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

            category = normalize_string(data.get("category", "floor")).lower()
            if category != "floor":
                return routes.make_response(
                    data={
                        "error": "Unsupported category '{}'. Only 'floor' is currently supported.".format(
                            category
                        )
                    },
                    status=400,
                )

            type_name = data.get("type_name")
            level_name = data.get("level_name")
            boundary = data.get("boundary", [])
            properties = data.get("properties", {})

            if not level_name:
                return routes.make_response(
                    data={"error": "No level_name provided"}, status=400
                )
            if not boundary or len(boundary) < 3:
                return routes.make_response(
                    data={"error": "boundary must contain at least 3 points"},
                    status=400,
                )

            level = _find_level_by_name(doc, level_name)
            if not level:
                return routes.make_response(
                    data={"error": "Level not found: {}".format(level_name)},
                    status=404,
                )

            floor_types = (
                DB.FilteredElementCollector(doc).OfClass(DB.FloorType).ToElements()
            )
            floor_type = None
            if type_name:
                for ft in floor_types:
                    if get_element_name(ft) == type_name:
                        floor_type = ft
                        break
                if not floor_type:
                    available = sorted(
                        [normalize_string(get_element_name(ft)) for ft in floor_types]
                    )
                    return routes.make_response(
                        data={
                            "error": "Floor type not found: {}".format(type_name),
                            "available_floor_types": available[:20],
                        },
                        status=404,
                    )
            else:
                floor_type = floor_types[0] if floor_types else None
                if not floor_type:
                    return routes.make_response(
                        data={"error": "No floor types available in the model"},
                        status=404,
                    )

            points = []
            for pt in boundary:
                if not all(k in pt for k in ["x", "y"]):
                    return routes.make_response(
                        data={"error": "Each boundary point must include x and y"},
                        status=400,
                    )
                points.append(
                    DB.XYZ(
                        meters_to_feet(float(pt["x"])),
                        meters_to_feet(float(pt["y"])),
                        0,
                    )
                )

            curve_loop = DB.CurveLoop()
            for i in range(len(points)):
                start_pt = points[i]
                end_pt = points[(i + 1) % len(points)]
                curve_loop.Append(DB.Line.CreateBound(start_pt, end_pt))

            t = DB.Transaction(doc, "Create Floor via MCP")
            t.Start()

            try:
                try:
                    # Revit 2022+ API
                    from System.Collections.Generic import List

                    curve_loops = List[DB.CurveLoop]()
                    curve_loops.Add(curve_loop)
                    new_floor = DB.Floor.Create(
                        doc, curve_loops, floor_type.Id, level.Id
                    )
                except AttributeError:
                    # Pre-2022 API
                    curve_array = DB.CurveArray()
                    for curve in curve_loop:
                        curve_array.Append(curve)
                    new_floor = doc.Create.NewFloor(
                        curve_array, floor_type, level, False
                    )

                properties_set, properties_failed = _apply_properties(
                    new_floor, properties
                )

                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "element_id": element_id_value(new_floor.Id),
                        "category": "floor",
                        "type_name": normalize_string(get_element_name(floor_type)),
                        "level": level_name,
                        "properties_set": properties_set,
                        "properties_failed": properties_failed,
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to create surface-based element: {}".format(str(e)))
            return routes.make_response(
                data={
                    "error": "Failed to create surface-based element: {}".format(
                        str(e)
                    )
                },
                status=500,
            )

    logger.info("Creation routes registered successfully")
