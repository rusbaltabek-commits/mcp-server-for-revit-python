---
name: add-revit-mcp-tool
description: Add a new MCP tool to this Revit MCP server project (a route in revit_mcp/ that runs inside Revit's IronPython, plus a matching MCP tool wrapper in tools/ that runs in main.py's Python 3 server). Use this whenever the user asks to add, implement, or wire up a new Revit capability/tool/endpoint for the MCP server — e.g. "add a tool for tagging walls", "implement create_line_based_element", "let the agent rename views", or when working through the Pending rows in README.md's tool status table. Also use it if something seems to be missing an MCP tool that a Revit MCP capability should expose.
---

# Adding a new Revit MCP tool

This project bridges an LLM (via MCP) to Revit through two layers that run in
**different Python runtimes**, talking over HTTP:

```
MCP client (LLM)  --MCP-->  tools/*.py (Python 3, in main.py)
                                    | HTTP (localhost:48884)
                                    v
                            revit_mcp/*.py (IronPython 2, inside Revit)
```

Every new capability needs **both halves** wired together, plus a unit test
and a README update. Skipping either half leaves a tool that either isn't
callable (MCP side missing) or silently 404s (route side missing).

## Before you start: read one existing pair

Don't write from scratch — copy the shape of an existing pair and adapt it.
[revit_mcp/elements.py](../../../revit_mcp/elements.py) +
[tools/element_tools.py](../../../tools/element_tools.py) is a good template
(modify/delete by ID, with the two encoding fixes below already applied).
[revit_mcp/creation.py](../../../revit_mcp/creation.py) +
[tools/creation_tools.py](../../../tools/creation_tools.py) is the template
if the new tool creates geometry.

## 1. The route side: `revit_mcp/<name>.py`

This file is loaded by pyRevit **inside Revit's IronPython 2 engine**, not by
this project's own `.venv`. That has real consequences:

- **No f-strings.** Use `"...{}...".format(x)` everywhere — IronPython 2
  doesn't have them.
- **Never touch a `DB.*` enum at module scope.** Something like
  `DB.BuiltInParameter.SOME_NAME` written directly in a module-level dict or
  constant runs *at import time*, when pyRevit registers all routes. If the
  enum name is wrong (easy to get wrong — Revit's API has a LOT of similarly
  named enums), the whole module fails to import, `startup.py`'s
  `register_routes()` re-raises, and pyRevit's extension loading can hang or
  fail outright — this actually happened once during development. Always
  resolve enum lookups **lazily, inside a function, via `getattr(...)`
  wrapped in try/except**, so a bad name degrades to "not found" instead of
  breaking everything. See `_find_parameter()` in `elements.py` for the
  pattern.
- **Register the route** by adding a
  `register_<name>_routes(api)` function that defines endpoints with
  `@api.route("/<endpoint>/", methods=[...])`, then import and call it from
  `startup.py`'s `register_routes()`, next to the existing calls.

### Two encoding bugs you must guard against

These were found and fixed the hard way — don't reintroduce them.

**Bug 1 — incoming POST bodies arrive mangled.** This pyRevit Routes
install's HTTP/JSON layer decodes the request body as Latin-1 instead of
UTF-8. Any non-ASCII text (Cyrillic level names, comments, family names —
this project's Revit installs are often Russian-localized) arrives with each
UTF-8 byte turned into a separate Latin-1 character. **Fix:** immediately
after parsing the body, run it through the existing helper:

```python
data = json.loads(request.data) if isinstance(request.data, str) else request.data
data = fix_mojibake_deep(data)   # from utils.py — do this for every POST route
```

**Bug 2 — writing text back out corrupts it too.** Calling `str(value)` on a
Python 2 `unicode` string containing non-ASCII characters mangles it under
IronPython 2 (a *different* bug from #1, happening on the way out instead of
in). **Fix:** whenever you set a `DB.StorageType.String` parameter, use:

```python
param.Set(to_param_string(param_value))   # from utils.py — never param.Set(str(param_value))
```

### Other conventions to follow

- Wrap any model mutation in a transaction with rollback on error:
  ```python
  t = DB.Transaction(doc, "Descriptive Name via MCP")
  t.Start()
  try:
      ...
      t.Commit()
      return routes.make_response(data={"status": "success", ...})
  except Exception as tx_error:
      if t.HasStarted() and not t.HasEnded():
          t.RollBack()
      raise tx_error
  ```
- Reuse the helpers already in `revit_mcp/utils.py` instead of reinventing
  them: `normalize_string` (safe text from .NET), `get_element_name`,
  `element_id_value` (handles the Revit-version differences in ElementId),
  `feet_to_meters` / `meters_to_feet`. This project surfaces coordinates to
  the LLM **in meters** (see `list_levels`, `get_selected_elements`) — keep
  new geometry-related tools consistent with that rather than exposing raw
  Revit feet, so an agent chaining multiple tools together doesn't silently
  mix units.
- On errors, return `routes.make_response(data={"error": "..."}, status=...)`
  with a specific, actionable message — the LLM only knows what's in that
  message, so vague errors lead to the agent guessing blindly (this happened
  during testing: a generic "not found" sent the agent on a long detour
  through ad-hoc code execution before the real fix was to just return the
  list of valid options in the error response).

## 2. The MCP tool side: `tools/<name>_tools.py`

This file runs in **Python 3**, as part of `main.py`'s FastMCP server — normal
modern Python is fine here (f-strings, type hints, etc.).

```python
# -*- coding: utf-8 -*-
from mcp.server.fastmcp import Context
from .utils import format_response


def register_<name>_tools(mcp, revit_get, revit_post):
    @mcp.tool()
    async def <tool_name>(param: type, ctx: Context = None) -> str:
        """Docstring the LLM reads to decide when/how to call this."""
        data = {...}
        response = await revit_post("/<endpoint>/", data, ctx)
        return format_response(response)
```

Write the tool's docstring for the *model*, not for a human reader of the
code — it's the primary thing the LLM sees when deciding whether and how to
call the tool. State units explicitly (e.g. "coordinates in meters") and any
non-obvious defaults or side effects.

Register the new `register_<name>_tools(...)` call in `tools/__init__.py`'s
`register_tools()`, alongside the existing ones.

## 3. Tests

Add a `Test<Name>Tools` class to
[tests/unit/test_tool_wrappers.py](../../../tests/unit/test_tool_wrappers.py),
following the existing pattern exactly — it's mechanical:

```python
class Test<Name>Tools:
    @pytest.fixture(autouse=True)
    def setup(self, mock_mcp, mock_revit_get, mock_revit_post):
        mock_revit_post.return_value = {"status": "success"}
        register_<name>_tools(mock_mcp, mock_revit_get, mock_revit_post)
        self.tools = mock_mcp.tools
        self.mock_post = mock_revit_post

    async def test_<tool_name>(self):
        await self.tools["<tool_name>"](param=value, ctx=None)
        self.mock_post.assert_called_once_with("/<endpoint>/", {expected payload}, None)
```

These tests only cover the Python 3 side (the route side runs inside Revit
and can't be unit tested outside it — that's what step 4 is for).

## 4. Verify, then remind the user to reload Revit

```bash
python -m py_compile revit_mcp/<name>.py tools/<name>_tools.py
uv run pytest tests/unit -q
```

The `py_compile` step catches syntax mistakes but **not** IronPython-specific
issues (missing enum names, API differences) — those only surface inside
Revit. So after everything passes:

1. Tell the user to **Reload pyRevit** (or restart Revit) — route files are
   only re-read on load, editing them live does nothing until reloaded.
2. Warn them that if the reload seems to hang, it's very likely a route
   module failing to import (see the enum-at-module-scope pitfall above) —
   check that no `DB.*` attribute access happens outside a function body in
   any new file before assuming something else is wrong.
3. Once reloaded, test the new tool live through the MCP client (e.g. ask
   the agent to call it directly: "Call `<tool_name>` with ..."). For
   anything accepting free text, test with non-ASCII (Cyrillic) input
   specifically — that's exactly the case the two encoding bugs above hide
   in, and a tool that only gets tested with English input can look fine
   and still be broken.

## 5. Update README.md

Find the tool's row in the status table and flip it from `🔄 Pending` to
`✅ Implemented`, updating the description if it's now more specific (e.g.
"currently: walls only" if you only implemented one category of a broader
Pending item).
