# -*- coding: utf-8 -*-
"""Material creation and assignment tools"""

from mcp.server.fastmcp import Context
from typing import List, Optional
from .utils import format_response


def register_material_tools(mcp, revit_post):
    """Register material tools"""

    @mcp.tool()
    async def create_material(
        name: str,
        color_rgb: Optional[List[int]] = None,
        transparency: Optional[int] = None,
        ctx: Context = None,
    ) -> str:
        """
        Find-or-create a material by name in the current Revit model.

        If a material with this name already exists, it is reused (and its
        color/transparency updated if given) rather than duplicated.

        Args:
            name: Material name (e.g. "Кирпич красный", "Glass - Blue Tint").
            color_rgb: Optional [r, g, b], each 0-255.
            transparency: Optional 0-100 percent (100 = fully transparent) -
                useful for glazing materials.
        """
        data = {"name": name}
        if color_rgb:
            data["color_rgb"] = color_rgb
        if transparency is not None:
            data["transparency"] = transparency
        response = await revit_post("/create_material/", data, ctx)
        return format_response(response)

    @mcp.tool()
    async def set_compound_layer_material(
        category: str,
        type_name: str,
        material_name: str,
        layer_index: int = 0,
        ctx: Context = None,
    ) -> str:
        """
        Apply a material to one compound-structure layer of a wall/floor/roof
        TYPE (not a single instance) - every element using that type updates
        from this one call, the same way editing a type in Revit's UI does.

        Args:
            category: "wall", "floor", or "roof".
            type_name: Exact type name (see the error's "available_types"
                if it doesn't match).
            material_name: Exact material name (create it first with
                create_material if it doesn't exist yet).
            layer_index: Which compound-structure layer to paint, 0-based.
                Defaults to 0, Revit's convention for the outermost/
                exterior-facing layer - use a different index for the core
                or an interior finish layer.
        """
        data = {
            "category": category,
            "type_name": type_name,
            "material_name": material_name,
            "layer_index": layer_index,
        }
        response = await revit_post("/set_compound_layer_material/", data, ctx)
        return format_response(response)
