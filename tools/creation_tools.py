# -*- coding: utf-8 -*-
"""Line-based and surface-based element creation tools"""

from mcp.server.fastmcp import Context
from typing import Dict, Any, List, Optional
from .utils import format_response


def register_creation_tools(mcp, revit_post):
    """Register element creation tools"""

    @mcp.tool()
    async def create_line_based_element(
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        level_name: str,
        category: str = "wall",
        type_name: Optional[str] = None,
        height: Optional[float] = None,
        properties: Dict[str, Any] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a line-based element between two points (currently only the
        'wall' category is supported). Coordinates and height are in meters.
        """
        data = {
            "category": category,
            "type_name": type_name,
            "level_name": level_name,
            "start": {"x": start_x, "y": start_y},
            "end": {"x": end_x, "y": end_y},
            "height": height,
            "properties": properties or {},
        }
        response = await revit_post("/create_line_based_element/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def create_surface_based_element(
        boundary: List[Dict[str, float]],
        level_name: str,
        category: str = "floor",
        type_name: Optional[str] = None,
        properties: Dict[str, Any] = None,
        ctx: Context = None,
    ) -> str:
        """
        Create a surface-based element from a closed boundary loop (currently
        only the 'floor' category is supported). `boundary` is an ordered
        list of {"x": ..., "y": ...} points in meters, at least 3 points,
        forming a closed polygon (it is closed automatically).
        """
        data = {
            "category": category,
            "type_name": type_name,
            "level_name": level_name,
            "boundary": boundary,
            "properties": properties or {},
        }
        response = await revit_post("/create_surface_based_element/", data, ctx)
        return format_response(response)
