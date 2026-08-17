# -*- coding: UTF-8 -*-
"""
Materials Module for Revit MCP
Find-or-create materials and apply them to wall/floor/roof type compound
layers, so a whole type's worth of instances updates from one call.
"""

from pyrevit import routes, DB
import json
import logging

from utils import (
    normalize_string,
    get_element_name,
    element_id_value,
    to_param_string,
    fix_mojibake_deep,
)

logger = logging.getLogger(__name__)

_CATEGORY_TYPE_CLASSES = {
    "wall": DB.WallType,
    "floor": DB.FloorType,
    "roof": DB.RoofType,
}


def _find_material_by_name(doc, name):
    target = normalize_string(name).strip().lower()
    materials = DB.FilteredElementCollector(doc).OfClass(DB.Material).ToElements()
    for mat in materials:
        try:
            if normalize_string(get_element_name(mat)).strip().lower() == target:
                return mat
        except Exception:
            continue
    return None


def _available_material_names(doc, limit=20):
    materials = DB.FilteredElementCollector(doc).OfClass(DB.Material).ToElements()
    return sorted(
        set(normalize_string(get_element_name(m)) for m in materials)
    )[:limit]


def _find_type_by_name(doc, type_class, type_name):
    """Find a type element by display name. Returns (type_elem, all_types)."""
    types = list(DB.FilteredElementCollector(doc).OfClass(type_class).ToElements())
    target = normalize_string(type_name).strip().lower()
    for t in types:
        try:
            if normalize_string(get_element_name(t)).strip().lower() == target:
                return t, types
        except Exception:
            continue
    return None, types


def _available_type_names(types, limit=20):
    return sorted(set(normalize_string(get_element_name(t)) for t in types))[:limit]


def register_material_routes(api):
    """Register material creation/assignment routes with the API"""

    @api.route("/create_material/", methods=["POST"])
    def create_material(doc, request):
        """
        Find-or-create a material by name. Optionally set its color and
        transparency (useful for tinted/clear glass).

        Expected request data:
        {
            "name": "Кирпич красный",
            "color_rgb": [178, 60, 40],
            "transparency": 0
        }

        "color_rgb" is an optional [r, g, b] list, 0-255 each.
        "transparency" is an optional 0-100 percent (100 = fully transparent).
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

            name = data.get("name")
            if not name:
                return routes.make_response(
                    data={"error": "No name provided"}, status=400
                )

            color_rgb = data.get("color_rgb")
            transparency = data.get("transparency")

            existing = _find_material_by_name(doc, name)
            if existing:
                mat = existing
                status = "reused"
                t = None
            else:
                t = DB.Transaction(doc, "Create Material via MCP")
                t.Start()
                try:
                    mat_id = DB.Material.Create(doc, to_param_string(name))
                    mat = doc.GetElement(mat_id)
                    status = "created"
                except Exception as tx_error:
                    if t.HasStarted() and not t.HasEnded():
                        t.RollBack()
                    raise tx_error

            try:
                needs_commit = False
                if t is None and (color_rgb or transparency is not None):
                    # Reused material but caller wants to (re)apply color/
                    # transparency - open a transaction for that alone.
                    t = DB.Transaction(doc, "Update Material via MCP")
                    t.Start()
                    needs_commit = True

                if color_rgb:
                    r, g, b = [int(c) for c in color_rgb]
                    mat.Color = DB.Color(r, g, b)
                if transparency is not None:
                    mat.Transparency = int(transparency)

                if t is not None and not t.HasEnded():
                    t.Commit()

                return routes.make_response(
                    data={
                        "status": status,
                        "element_id": element_id_value(mat.Id),
                        "name": normalize_string(get_element_name(mat)),
                    }
                )
            except Exception as tx_error:
                if t is not None and t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to create material: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to create material: {}".format(str(e))},
                status=500,
            )

    @api.route("/set_compound_layer_material/", methods=["POST"])
    def set_compound_layer_material(doc, request):
        """
        Apply a material to one compound-structure layer of a wall/floor/roof
        TYPE, so every instance of that type updates from a single call.

        Expected request data:
        {
            "category": "wall",
            "type_name": "Generic - 200mm",
            "material_name": "Кирпич красный",
            "layer_index": 0
        }

        "layer_index" defaults to 0, Revit's convention for the outermost/
        exterior-facing layer in a compound structure - override for other
        layers (core, interior finish, etc).
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

            category = normalize_string(data.get("category", "")).lower()
            type_name = data.get("type_name")
            material_name = data.get("material_name")
            layer_index = int(data.get("layer_index", 0))

            type_class = _CATEGORY_TYPE_CLASSES.get(category)
            if not type_class:
                return routes.make_response(
                    data={
                        "error": "Unsupported category '{}'. Use 'wall', 'floor' or 'roof'.".format(
                            category
                        )
                    },
                    status=400,
                )
            if not type_name:
                return routes.make_response(
                    data={"error": "No type_name provided"}, status=400
                )
            if not material_name:
                return routes.make_response(
                    data={"error": "No material_name provided"}, status=400
                )

            type_elem, all_types = _find_type_by_name(doc, type_class, type_name)
            if not type_elem:
                return routes.make_response(
                    data={
                        "error": "{} type not found: {}".format(category, type_name),
                        "available_types": _available_type_names(all_types),
                    },
                    status=404,
                )

            material = _find_material_by_name(doc, material_name)
            if not material:
                return routes.make_response(
                    data={
                        "error": "Material not found: {}".format(material_name),
                        "available_materials": _available_material_names(doc),
                    },
                    status=404,
                )

            try:
                structure = type_elem.GetCompoundStructure()
            except Exception:
                structure = None
            if not structure:
                return routes.make_response(
                    data={
                        "error": "{} type '{}' has no compound structure (simple/"
                        "system family types can't be layer-painted this way)".format(
                            category, type_name
                        )
                    },
                    status=400,
                )

            try:
                layer_count = structure.LayerCount
            except AttributeError:
                layer_count = len(list(structure.GetLayers()))

            if layer_index < 0 or layer_index >= layer_count:
                return routes.make_response(
                    data={
                        "error": "layer_index {} out of range - type has {} layer(s)".format(
                            layer_index, layer_count
                        ),
                        "layer_count": layer_count,
                    },
                    status=400,
                )

            t = DB.Transaction(doc, "Set Compound Layer Material via MCP")
            t.Start()
            try:
                structure.SetLayerMaterialId(layer_index, material.Id)
                type_elem.SetCompoundStructure(structure)
                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "category": category,
                        "type_name": normalize_string(get_element_name(type_elem)),
                        "material_name": normalize_string(get_element_name(material)),
                        "layer_index": layer_index,
                        "layer_count": layer_count,
                    }
                )
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error(
                "Failed to set compound layer material: {}".format(str(e))
            )
            return routes.make_response(
                data={
                    "error": "Failed to set compound layer material: {}".format(
                        str(e)
                    )
                },
                status=500,
            )

    logger.info("Material routes registered successfully")
