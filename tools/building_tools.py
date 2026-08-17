# -*- coding: utf-8 -*-
"""Whole-building generator tool: levels, walls, openings, floors, roofs, rooms"""

from mcp.server.fastmcp import Context
from typing import Any, Dict, List, Optional
from .utils import format_response


def register_building_tools(mcp, revit_post):
    """Register the whole-building generator tool"""

    @mcp.tool()
    async def generate_building(
        levels: List[Dict[str, Any]],
        walls: Optional[List[Dict[str, Any]]] = None,
        openings: Optional[List[Dict[str, Any]]] = None,
        floors: Optional[List[Dict[str, Any]]] = None,
        roofs: Optional[List[Dict[str, Any]]] = None,
        rooms: Optional[List[Dict[str, Any]]] = None,
        ctx: Context = None,
    ) -> str:
        """
        Generate a whole building in one shot from a structured description,
        instead of creating elements one MCP call at a time. All coordinates,
        lengths, elevations and heights are in meters. Everything is built in
        a single Revit transaction; individual items that fail (missing
        level, unknown family/type, degenerate geometry, ...) are skipped and
        reported back rather than aborting the whole building.

        levels: [{"name": "Level 1", "elevation": 0.0}, ...]
            Elevation in meters. A level is created if no level with that
            name exists yet, otherwise the existing one is reused.

        walls: [{"id": "W1", "level": "Level 1",
                 "start": {"x": 0.0, "y": 0.0}, "end": {"x": 6.0, "y": 0.0},
                 "height": 3.0, "type_name": "Generic - 200mm"}, ...]
            "id" is caller-defined and used to host openings on this wall.
            "height" and "type_name" are optional: height defaults to the
            gap to the next level up (else 3m), type_name defaults to the
            model's first available wall type.

        openings: [{"host_wall": "W1", "kind": "door",
                    "distance_along_wall": 1.5, "family_name": "Single-Flush",
                    "type_name": "0915 x 2134mm", "sill_height": 0.0}, ...]
            "host_wall" must match a wall's "id". "kind" is "door" or
            "window". "distance_along_wall" is measured in meters from the
            wall's start point. "sill_height" (meters) only applies to
            windows.

        floors: [{"level": "Level 1", "type_name": "Generic 150mm",
                  "boundary": [{"x":0,"y":0}, {"x":6,"y":0}, {"x":6,"y":4}, {"x":0,"y":4}]}, ...]
            "boundary" is an ordered list of at least 3 points forming a
            closed polygon (closed automatically). "type_name" is optional.

        roofs: [{"level": "Level 2", "type_name": "Generic - 400mm",
                 "boundary": [...]}, ...]
            Same boundary convention as floors; built as a flat footprint
            roof at the given level.

        rooms: [{"level": "Level 1", "name": "Гостиная", "point": {"x": 3.0, "y": 2.0}}, ...]
            "point" must fall inside a closed loop of walls at that level or
            room creation is skipped for that entry.
        """
        data = {
            "levels": levels or [],
            "walls": walls or [],
            "openings": openings or [],
            "floors": floors or [],
            "roofs": roofs or [],
            "rooms": rooms or [],
        }
        response = await revit_post("/generate_building/", data, ctx)
        return format_response(response)
