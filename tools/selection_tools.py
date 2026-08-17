# -*- coding: utf-8 -*-
"""Selection tools"""

from mcp.server.fastmcp import Context
from .utils import format_response


def register_selection_tools(mcp, revit_get):
    """Register selection-related tools"""

    @mcp.tool()
    async def get_selected_elements(ctx: Context = None) -> str:
        """
        Get information about the elements currently selected in the active Revit UI.

        Each element includes its location in meters (a point, or a start/end
        curve for line-based elements like walls). Use this to read where an
        existing element is before calling create_line_based_element or
        create_surface_based_element relative to it, instead of asking the
        user for raw coordinates.
        """
        response = await revit_get("/selected_elements/", ctx)
        return format_response(response)
