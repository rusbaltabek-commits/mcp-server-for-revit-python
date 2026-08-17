# -*- coding: utf-8 -*-
"""Element modification and deletion tools"""

from mcp.server.fastmcp import Context
from typing import Dict, Any, List
from .utils import format_response


def register_element_tools(mcp, revit_get, revit_post):
    """Register element management tools"""

    @mcp.tool()
    async def modify_element(
        element_id: int,
        parameters: Dict[str, Any],
        ctx: Context = None,
    ) -> str:
        """Modify instance parameters of an existing Revit element, by its element ID"""
        data = {"element_id": element_id, "parameters": parameters}
        response = await revit_post("/modify_element/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def delete_elements(
        element_ids: List[int],
        ctx: Context = None,
    ) -> str:
        """Delete one or more elements from the Revit model, by their element IDs"""
        data = {"element_ids": element_ids}
        response = await revit_post("/delete_elements/", data, ctx)
        return format_response(response)
