# -*- coding: UTF-8 -*-
"""
Selection Module for Revit MCP
Provides information about the elements currently selected in the active UI
"""

from pyrevit import routes
import logging

from utils import normalize_string, get_element_name, element_id_value, feet_to_meters

logger = logging.getLogger(__name__)


def _point_to_meters(pt):
    return {
        "x": round(feet_to_meters(pt.X), 3),
        "y": round(feet_to_meters(pt.Y), 3),
        "z": round(feet_to_meters(pt.Z), 3),
    }


def _get_location(elem):
    """Get an element's placement location, in meters.

    Returns {"type": "point", "x", "y", "z"} for point-based elements
    (families, etc.) or {"type": "curve", "start": {...}, "end": {...}}
    for line-based elements (walls, etc.) - matching the coordinate shape
    expected by create_line_based_element/create_surface_based_element, so
    a caller can read a selection's location and feed it straight into
    those tools.
    """
    try:
        location = elem.Location
        if hasattr(location, "Point"):
            result = {"type": "point"}
            result.update(_point_to_meters(location.Point))
            return result
        if hasattr(location, "Curve"):
            curve = location.Curve
            return {
                "type": "curve",
                "start": _point_to_meters(curve.GetEndPoint(0)),
                "end": _point_to_meters(curve.GetEndPoint(1)),
            }
    except Exception:
        pass
    return {"type": "unknown"}


def register_selection_routes(api):
    """Register selection-related routes with the API"""

    @api.route("/selected_elements/", methods=["GET"])
    def get_selected_elements(doc, uidoc):
        """
        Get information about the elements currently selected in the active
        Revit UI (i.e. whatever the user has clicked/highlighted).

        Each element includes a "location" (in meters) shaped to match the
        input expected by create_line_based_element/create_surface_based_element
        - {"type": "point", "x", "y", "z"} or
        {"type": "curve", "start": {...}, "end": {...}} - so a caller can
        read where an existing element is and create new geometry relative
        to it without the user having to supply raw coordinates.

        Returns:
            dict: List of selected elements with id, category, type name,
            and location
        """
        try:
            if not doc or not uidoc:
                return routes.make_response(
                    data={"error": "No active Revit document"}, status=503
                )

            selected_ids = uidoc.Selection.GetElementIds()

            elements_info = []
            for element_id in selected_ids:
                try:
                    elem = doc.GetElement(element_id)
                    if not elem:
                        continue

                    cat = elem.Category
                    cat_name = cat.Name if cat else "Unknown"

                    element_info = {
                        "element_id": element_id_value(elem.Id),
                        "name": normalize_string(get_element_name(elem)),
                        "category": cat_name,
                    }

                    # Include the family/type name when available
                    try:
                        elem_type = doc.GetElement(elem.GetTypeId())
                        if elem_type:
                            element_info["type_name"] = normalize_string(
                                get_element_name(elem_type)
                            )
                    except Exception:
                        pass

                    element_info["location"] = _get_location(elem)

                    elements_info.append(element_info)

                except Exception as elem_error:
                    logger.warning(
                        "Could not process selected element: {}".format(
                            str(elem_error)
                        )
                    )
                    continue

            return routes.make_response(
                data={
                    "status": "success",
                    "count": len(elements_info),
                    "elements": elements_info,
                }
            )

        except Exception as e:
            logger.error("Failed to get selected elements: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to get selected elements: {}".format(str(e))},
                status=500,
            )

    logger.info("Selection routes registered successfully")
