# -*- coding: UTF-8 -*-
"""
Curtain Wall Facade Module for Revit MCP
Grid layout/spacing and mullion-profile control for curtain-wall TYPES.

Curtain walls are already creatable through the existing wall pipeline
(create_line_based_element / generate_building's "walls", by passing the
name of a curtain-kind WallType) - this module only adds the facade-pattern
controls that path doesn't cover: bay spacing and mullion profiles.

NOTE ON BuiltInParameter NAMES: Revit stores the vertical and horizontal
grid-layout/mullion parameters of a curtain-wall type under *separate*
BuiltInParameter enum members that happen to share the same UI display
name (both directions' interior-mullion field is labelled "Interior Type",
for example). This was not verified against a live Revit session while
writing this file (Revit wasn't running at the time). Every lookup below
therefore tries a short list of plausible BuiltInParameter names first,
then falls back to scanning the type's actual parameters by display name
and parameter group to disambiguate direction. If nothing resolves, the
route returns a clear error listing exactly what was tried, instead of
silently guessing wrong or crashing at import time. Confirm the
"resolved_via" field in a real response the first time this runs against a
live model, and tighten the candidate lists if a fallback path was needed.
"""

from pyrevit import routes, DB
import json
import logging

from utils import (
    normalize_string,
    get_element_name,
    element_id_value,
    meters_to_feet,
    fix_mojibake_deep,
)

logger = logging.getLogger(__name__)

_LAYOUT_ENUM_NAMES = {
    "none": "None",
    "fixed_distance": "FixedDistance",
    "fixed_number": "FixedNumber",
    "maximum_spacing": "MaximumSpacing",
    "minimum_spacing": "MinimumSpacing",
}

_GRID_LAYOUT_CANDIDATES = {
    "vertical": ["CURTAIN_GRID_LAYOUT_VERT"],
    "horizontal": ["CURTAIN_GRID_LAYOUT_HORIZ"],
}
_GRID_SPACING_CANDIDATES = {
    "vertical": ["CURTAIN_GRID_SPACING_VERT"],
    "horizontal": ["CURTAIN_GRID_SPACING_HORIZ"],
}
_GRID_NUMBER_CANDIDATES = {
    "vertical": ["CURTAIN_GRID_NUM_VERT", "CURTAIN_GRID_SPACING_VERT"],
    "horizontal": ["CURTAIN_GRID_NUM_HORIZ", "CURTAIN_GRID_SPACING_HORIZ"],
}
_MULLION_CANDIDATES = {
    ("interior", "vertical"): [
        "CURTAIN_GRID_MULLION_INTERIOR_VERT",
        "AUTO_MULLION_INTERIOR_VERT",
        "CURTAIN_WALL_MULLION_INTERIOR_VERT",
    ],
    ("interior", "horizontal"): [
        "CURTAIN_GRID_MULLION_INTERIOR_HORIZ",
        "AUTO_MULLION_INTERIOR_HORIZ",
        "CURTAIN_WALL_MULLION_INTERIOR_HORIZ",
    ],
    ("border1", "vertical"): [
        "CURTAIN_GRID_MULLION_BORDER1_VERT",
        "AUTO_MULLION_BORDER1_VERT",
    ],
    ("border1", "horizontal"): [
        "CURTAIN_GRID_MULLION_BORDER1_HORIZ",
        "AUTO_MULLION_BORDER1_HORIZ",
    ],
    ("border2", "vertical"): [
        "CURTAIN_GRID_MULLION_BORDER2_VERT",
        "AUTO_MULLION_BORDER2_VERT",
    ],
    ("border2", "horizontal"): [
        "CURTAIN_GRID_MULLION_BORDER2_HORIZ",
        "AUTO_MULLION_BORDER2_HORIZ",
    ],
}
_MULLION_DISPLAY_NAMES = {
    "interior": set(["interior type"]),
    "border1": set(["border 1 type"]),
    "border2": set(["border 2 type"]),
}


def _is_curtain_kind(wall_type):
    try:
        return wall_type.Kind == DB.WallKind.Curtain
    except Exception:
        return False


def _find_curtain_wall_type_by_name(doc, type_name):
    """Find a curtain-wall-kind WallType by name. Returns (type, curtain_types)."""
    all_types = list(DB.FilteredElementCollector(doc).OfClass(DB.WallType).ToElements())
    curtain_types = [t for t in all_types if _is_curtain_kind(t)]
    target = normalize_string(type_name).strip().lower()
    for t in curtain_types:
        try:
            if normalize_string(get_element_name(t)).strip().lower() == target:
                return t, curtain_types
        except Exception:
            continue
    return None, curtain_types


def _available_names(types, limit=20):
    return sorted(set(normalize_string(get_element_name(t)) for t in types))[:limit]


def _resolve_param(type_elem, bip_candidates, display_name_candidates=None, group_hint=None):
    """
    Try BuiltInParameter name candidates first; fall back to scanning the
    type's actual parameters by display name (optionally narrowed by a
    group-label hint) when direction/position is ambiguous by name alone.
    Returns (Parameter, resolved_via_description) or (None, None).
    """
    for name in bip_candidates:
        bip = getattr(DB.BuiltInParameter, name, None)
        if bip is None:
            continue
        try:
            param = type_elem.get_Parameter(bip)
        except Exception:
            param = None
        if param is not None:
            return param, "BuiltInParameter.{}".format(name)

    if display_name_candidates:
        matches = []
        try:
            for param in type_elem.Parameters:
                try:
                    pname = normalize_string(param.Definition.Name).strip().lower()
                except Exception:
                    continue
                if pname in display_name_candidates:
                    matches.append(param)
        except Exception:
            matches = []

        if len(matches) == 1:
            return matches[0], "display name '{}'".format(matches[0].Definition.Name)

        if len(matches) > 1 and group_hint:
            for param in matches:
                try:
                    group_label = DB.LabelUtils.GetLabelFor(param.Definition.ParameterGroup)
                except Exception:
                    group_label = None
                if group_label and group_hint.lower() in normalize_string(group_label).lower():
                    return param, "display name + group '{}'".format(group_label)

    return None, None


def register_curtain_wall_routes(api):
    """Register curtain-wall grid/mullion routes with the API"""

    @api.route("/set_curtain_wall_grid/", methods=["POST"])
    def set_curtain_wall_grid(doc, request):
        """
        Set the automatic grid layout/spacing on a curtain-wall TYPE (every
        instance of that type updates at once).

        Expected request data:
        {
            "type_name": "Storefront",
            "direction": "vertical",
            "layout": "fixed_distance",
            "spacing": 1.5,
            "number": null
        }

        "direction": "vertical" or "horizontal".
        "layout": "none" | "fixed_distance" | "fixed_number" |
            "maximum_spacing" | "minimum_spacing".
        "spacing" (meters) is required for fixed_distance/maximum_spacing/
        minimum_spacing. "number" (int) is required for fixed_number.
        type_name must name an existing curtain-wall-kind type - there is
        no default, unlike other creation tools, since defaulting to a
        non-curtain type would silently do nothing.
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

            type_name = data.get("type_name")
            direction = normalize_string(data.get("direction", "")).lower()
            layout = normalize_string(data.get("layout", "")).lower()
            spacing = data.get("spacing")
            number = data.get("number")

            if not type_name:
                return routes.make_response(
                    data={"error": "No type_name provided"}, status=400
                )
            if direction not in ("vertical", "horizontal"):
                return routes.make_response(
                    data={"error": "direction must be 'vertical' or 'horizontal'"},
                    status=400,
                )
            if layout not in _LAYOUT_ENUM_NAMES:
                return routes.make_response(
                    data={
                        "error": "layout must be one of: {}".format(
                            ", ".join(sorted(_LAYOUT_ENUM_NAMES.keys()))
                        )
                    },
                    status=400,
                )

            type_elem, curtain_types = _find_curtain_wall_type_by_name(doc, type_name)
            if not type_elem:
                return routes.make_response(
                    data={
                        "error": "Curtain-wall type not found: {}".format(type_name),
                        "available_curtain_wall_types": _available_names(curtain_types),
                    },
                    status=404,
                )

            layout_param, layout_via = _resolve_param(
                type_elem,
                _GRID_LAYOUT_CANDIDATES[direction],
                display_name_candidates=set(["layout"]),
                group_hint=direction,
            )
            if not layout_param:
                return routes.make_response(
                    data={
                        "error": (
                            "Could not locate the {} grid layout parameter on "
                            "this type. Tried: {}. Set it manually in Revit: "
                            "Edit Type > {} Grid Pattern > Layout."
                        ).format(
                            direction,
                            ", ".join(_GRID_LAYOUT_CANDIDATES[direction]),
                            direction.capitalize(),
                        )
                    },
                    status=500,
                )

            layout_enum_name = _LAYOUT_ENUM_NAMES[layout]
            layout_enum = getattr(DB.CurtainGridLayout, layout_enum_name, None)
            if layout_enum is None:
                return routes.make_response(
                    data={
                        "error": "DB.CurtainGridLayout.{} not found in this Revit API version".format(
                            layout_enum_name
                        )
                    },
                    status=500,
                )

            applied = {}
            t = DB.Transaction(doc, "Set Curtain Wall Grid via MCP")
            t.Start()
            try:
                layout_param.Set(int(layout_enum))
                applied["layout"] = {"value": layout, "resolved_via": layout_via}

                if layout in ("fixed_distance", "maximum_spacing", "minimum_spacing"):
                    if spacing is None:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "layout '{}' requires 'spacing' (meters)".format(layout)},
                            status=400,
                        )
                    spacing_param, spacing_via = _resolve_param(
                        type_elem,
                        _GRID_SPACING_CANDIDATES[direction],
                        display_name_candidates=set(["spacing"]),
                        group_hint=direction,
                    )
                    if not spacing_param:
                        t.RollBack()
                        return routes.make_response(
                            data={
                                "error": "Could not locate the {} grid spacing parameter. Tried: {}".format(
                                    direction, ", ".join(_GRID_SPACING_CANDIDATES[direction])
                                )
                            },
                            status=500,
                        )
                    spacing_param.Set(meters_to_feet(float(spacing)))
                    applied["spacing"] = {"value_m": spacing, "resolved_via": spacing_via}

                elif layout == "fixed_number":
                    if number is None:
                        t.RollBack()
                        return routes.make_response(
                            data={"error": "layout 'fixed_number' requires 'number' (int)"},
                            status=400,
                        )
                    number_param, number_via = _resolve_param(
                        type_elem,
                        _GRID_NUMBER_CANDIDATES[direction],
                        display_name_candidates=set(["number", "spacing"]),
                        group_hint=direction,
                    )
                    if not number_param:
                        t.RollBack()
                        return routes.make_response(
                            data={
                                "error": "Could not locate the {} grid number parameter. Tried: {}".format(
                                    direction, ", ".join(_GRID_NUMBER_CANDIDATES[direction])
                                )
                            },
                            status=500,
                        )
                    number_param.Set(int(number))
                    applied["number"] = {"value": number, "resolved_via": number_via}

                t.Commit()
                return routes.make_response(
                    data={
                        "status": "success",
                        "type_name": normalize_string(get_element_name(type_elem)),
                        "direction": direction,
                        "applied": applied,
                    }
                )
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to set curtain wall grid: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to set curtain wall grid: {}".format(str(e))},
                status=500,
            )

    @api.route("/set_curtain_wall_mullions/", methods=["POST"])
    def set_curtain_wall_mullions(doc, request):
        """
        Set the automatic mullion profile on a curtain-wall TYPE (every
        panel-to-panel joint of that type updates at once).

        Expected request data:
        {
            "type_name": "Storefront",
            "mullion_type_name": "Rectangular Mullion 1.5\" x 2.5\"",
            "position": "interior",
            "direction": "vertical"
        }

        "position": "interior" | "border1" | "border2".
        "direction": "vertical" | "horizontal". Horizontal mullions may not
        be reachable through the Revit API on every version/build; if this
        route can't locate the parameter it says so explicitly rather than
        silently doing nothing, and names the manual Edit Type fallback.
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

            type_name = data.get("type_name")
            mullion_type_name = data.get("mullion_type_name")
            position = normalize_string(data.get("position", "")).lower()
            direction = normalize_string(data.get("direction", "")).lower()

            if not type_name:
                return routes.make_response(
                    data={"error": "No type_name provided"}, status=400
                )
            if not mullion_type_name:
                return routes.make_response(
                    data={"error": "No mullion_type_name provided"}, status=400
                )
            if position not in ("interior", "border1", "border2"):
                return routes.make_response(
                    data={"error": "position must be 'interior', 'border1' or 'border2'"},
                    status=400,
                )
            if direction not in ("vertical", "horizontal"):
                return routes.make_response(
                    data={"error": "direction must be 'vertical' or 'horizontal'"},
                    status=400,
                )

            type_elem, curtain_types = _find_curtain_wall_type_by_name(doc, type_name)
            if not type_elem:
                return routes.make_response(
                    data={
                        "error": "Curtain-wall type not found: {}".format(type_name),
                        "available_curtain_wall_types": _available_names(curtain_types),
                    },
                    status=404,
                )

            mullion_types = list(
                DB.FilteredElementCollector(doc).OfClass(DB.MullionType).ToElements()
            )
            target = normalize_string(mullion_type_name).strip().lower()
            mullion_type = None
            for mt in mullion_types:
                try:
                    if normalize_string(get_element_name(mt)).strip().lower() == target:
                        mullion_type = mt
                        break
                except Exception:
                    continue
            if not mullion_type:
                return routes.make_response(
                    data={
                        "error": "Mullion type not found: {}".format(mullion_type_name),
                        "available_mullion_types": _available_names(mullion_types),
                    },
                    status=404,
                )

            param, resolved_via = _resolve_param(
                type_elem,
                _MULLION_CANDIDATES[(position, direction)],
                display_name_candidates=_MULLION_DISPLAY_NAMES[position],
                group_hint=direction,
            )
            if not param:
                return routes.make_response(
                    data={
                        "error": (
                            "Could not locate the {} {} mullion parameter on this "
                            "type. Tried: {}. This may be a real Revit API "
                            "limitation for this direction/build - set it "
                            "manually: select a panel > Edit Type > {} Mullions "
                            "> {} Type."
                        ).format(
                            direction,
                            position,
                            ", ".join(_MULLION_CANDIDATES[(position, direction)]),
                            direction.capitalize(),
                            position.capitalize(),
                        )
                    },
                    status=500,
                )

            t = DB.Transaction(doc, "Set Curtain Wall Mullions via MCP")
            t.Start()
            try:
                param.Set(mullion_type.Id)
                t.Commit()
                return routes.make_response(
                    data={
                        "status": "success",
                        "type_name": normalize_string(get_element_name(type_elem)),
                        "mullion_type_name": normalize_string(get_element_name(mullion_type)),
                        "position": position,
                        "direction": direction,
                        "resolved_via": resolved_via,
                    }
                )
            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to set curtain wall mullions: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to set curtain wall mullions: {}".format(str(e))},
                status=500,
            )

    logger.info("Curtain wall routes registered successfully")
