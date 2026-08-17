# -*- coding: utf-8 -*-
"""Curtain wall grid layout and mullion tools"""

from mcp.server.fastmcp import Context
from typing import Optional
from .utils import format_response


def register_curtain_wall_tools(mcp, revit_post):
    """Register curtain wall facade tools"""

    @mcp.tool()
    async def set_curtain_wall_grid(
        type_name: str,
        direction: str,
        layout: str,
        spacing: Optional[float] = None,
        number: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Set the automatic grid layout/spacing on a curtain-wall TYPE - every
        instance of that type updates at once. Curtain walls themselves are
        created via create_line_based_element/generate_building's "walls"
        using a curtain-kind type_name; this tool only controls the bay
        pattern.

        Args:
            type_name: Exact name of an existing curtain-wall-kind wall
                type (no default - see the error's
                "available_curtain_wall_types" if it doesn't match).
            direction: "vertical" or "horizontal".
            layout: "none", "fixed_distance", "fixed_number",
                "maximum_spacing", or "minimum_spacing".
            spacing: Bay spacing in meters - required for fixed_distance/
                maximum_spacing/minimum_spacing.
            number: Bay count - required for fixed_number.
        """
        data = {
            "type_name": type_name,
            "direction": direction,
            "layout": layout,
        }
        if spacing is not None:
            data["spacing"] = spacing
        if number is not None:
            data["number"] = number
        response = await revit_post("/set_curtain_wall_grid/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def set_curtain_wall_mullions(
        type_name: str,
        mullion_type_name: str,
        position: str,
        direction: str,
        ctx: Context = None,
    ) -> str:
        """
        Set the automatic mullion profile on a curtain-wall TYPE - every
        panel-to-panel joint of that type updates at once.

        Args:
            type_name: Exact name of an existing curtain-wall-kind wall
                type (see "available_curtain_wall_types" in the error if it
                doesn't match).
            mullion_type_name: Exact name of an existing mullion type (see
                "available_mullion_types" in the error if it doesn't match).
            position: "interior", "border1", or "border2".
            direction: "vertical" or "horizontal". Horizontal mullions may
                not be settable through the API on every Revit build/
                version - if so, the error explains the manual fallback.
        """
        data = {
            "type_name": type_name,
            "mullion_type_name": mullion_type_name,
            "position": position,
            "direction": direction,
        }
        response = await revit_post("/set_curtain_wall_mullions/", data, ctx)
        return format_response(response)
