# -*- coding: UTF-8 -*-
"""
Element Management Module for Revit MCP
Handles modifying instance parameters of, and deleting, existing elements
"""

from pyrevit import routes, DB
import json
import logging

from utils import (
    element_id_value,
    make_element_id,
    normalize_string,
    to_param_string,
    fix_mojibake_deep,
)

logger = logging.getLogger(__name__)

# LookupParameter() matches on the parameter's *display* name, which is
# localized (e.g. "Comments" shows as "Комментарии" in a Russian Revit
# install). For the handful of identity-data parameters almost every
# element has, fall back to their locale-independent BuiltInParameter id
# so callers don't have to guess the UI language. Resolved lazily (by name,
# via getattr) so a lookup problem can never break module import.
_COMMON_BUILTIN_PARAM_NAMES = {
    "comments": "ALL_MODEL_INSTANCE_COMMENTS",
    "mark": "ALL_MODEL_MARK",
}


def _find_parameter(elem, param_name):
    """Find an instance parameter by name, tolerant of Revit's UI language."""
    param = elem.LookupParameter(param_name)
    if param:
        return param

    bip_name = _COMMON_BUILTIN_PARAM_NAMES.get(param_name.strip().lower())
    if bip_name:
        try:
            bip = getattr(DB.BuiltInParameter, bip_name)
            return elem.get_Parameter(bip)
        except Exception:
            return None
    return None


def _list_parameter_names(elem):
    """List the display names of every instance parameter on an element."""
    names = []
    try:
        for param in elem.Parameters:
            try:
                names.append(normalize_string(param.Definition.Name))
            except Exception:
                continue
    except Exception:
        pass
    return sorted(set(names))


def _set_parameter(elem, param_name, param_value):
    """Try to set a single instance parameter, returning (ok, reason_if_failed)."""
    param = _find_parameter(elem, param_name)
    if not param:
        return False, "not found"
    if param.IsReadOnly:
        return False, "read-only"

    if param.StorageType == DB.StorageType.String:
        param.Set(to_param_string(param_value))
    elif param.StorageType == DB.StorageType.Integer:
        param.Set(int(param_value))
    elif param.StorageType == DB.StorageType.Double:
        param.Set(float(param_value))
    elif param.StorageType == DB.StorageType.ElementId:
        param.Set(make_element_id(param_value))
    else:
        return False, "unsupported type"

    return True, None


def register_element_routes(api):
    """Register element management routes with the API"""

    @api.route("/modify_element/", methods=["POST"])
    def modify_element(doc, request):
        """
        Modify instance parameters of an existing element.

        Expected request data:
        {
            "element_id": 123456,
            "parameters": {
                "Comments": "Updated via MCP",
                "Mark": "A1"
            }
        }
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

            element_id = data.get("element_id")
            parameters = data.get("parameters", {})

            if element_id is None:
                return routes.make_response(
                    data={"error": "No element_id provided"}, status=400
                )
            if not parameters:
                return routes.make_response(
                    data={"error": "No parameters provided to modify"}, status=400
                )

            elem = doc.GetElement(make_element_id(element_id))
            if not elem:
                return routes.make_response(
                    data={"error": "Element not found: {}".format(element_id)},
                    status=404,
                )

            t = DB.Transaction(doc, "Modify Element via MCP")
            t.Start()

            try:
                properties_set = []
                properties_failed = []

                for param_name, param_value in parameters.items():
                    try:
                        ok, reason = _set_parameter(elem, param_name, param_value)
                        if ok:
                            properties_set.append(param_name)
                        else:
                            properties_failed.append(
                                "{} ({})".format(param_name, reason)
                            )
                    except Exception as param_error:
                        properties_failed.append(
                            "{} (error: {})".format(param_name, str(param_error))
                        )

                t.Commit()

                response_data = {
                    "status": "success",
                    "element_id": element_id_value(elem.Id),
                    "properties_set": properties_set,
                    "properties_failed": properties_failed,
                }

                # If anything failed because the name wasn't recognized, help
                # the caller self-correct by listing what's actually there.
                if any("(not found)" in f for f in properties_failed):
                    response_data["available_parameters"] = _list_parameter_names(elem)

                return routes.make_response(data=response_data)

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to modify element: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to modify element: {}".format(str(e))},
                status=500,
            )

    @api.route("/delete_elements/", methods=["POST"])
    def delete_elements(doc, request):
        """
        Delete one or more elements from the model.

        Expected request data:
        {
            "element_ids": [123456, 789012]
        }
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

            element_ids = data.get("element_ids", [])
            if not element_ids:
                return routes.make_response(
                    data={"error": "No element_ids provided"}, status=400
                )

            t = DB.Transaction(doc, "Delete Elements via MCP")
            t.Start()

            try:
                deleted = []
                failed = []

                for eid in element_ids:
                    try:
                        element_id_obj = make_element_id(eid)
                        elem = doc.GetElement(element_id_obj)
                        if not elem:
                            failed.append({"element_id": eid, "reason": "not found"})
                            continue

                        doc.Delete(element_id_obj)
                        deleted.append(int(eid))
                    except Exception as del_error:
                        failed.append({"element_id": eid, "reason": str(del_error)})

                t.Commit()

                return routes.make_response(
                    data={
                        "status": "success",
                        "deleted": deleted,
                        "deleted_count": len(deleted),
                        "failed": failed,
                    }
                )

            except Exception as tx_error:
                if t.HasStarted() and not t.HasEnded():
                    t.RollBack()
                raise tx_error

        except Exception as e:
            logger.error("Failed to delete elements: {}".format(str(e)))
            return routes.make_response(
                data={"error": "Failed to delete elements: {}".format(str(e))},
                status=500,
            )

    logger.info("Element management routes registered successfully")
