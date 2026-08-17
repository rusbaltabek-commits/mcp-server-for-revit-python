# -*- coding: utf-8 -*-
from pyrevit import DB
import System
import traceback
import logging

logger = logging.getLogger(__name__)


def normalize_string(text):
    """Safely normalize string values, always returning a unicode string.

    In IronPython 2, calling str() on a .NET System.String that contains
    non-ASCII characters (e.g. accented letters) produces a byte string
    encoded with the system default codec.  The pyRevit Routes JSON encoder
    then fails with 'unknown codec can't decode byte 0xNN'.

    By returning unicode we guarantee the JSON serialiser receives a proper
    text object regardless of the locale of the Revit model.
    """
    if text is None:
        return u"Unnamed"
    # Already a unicode string (normal case for .NET System.String in IronPython)
    if isinstance(text, unicode):
        return text.strip()
    # Byte string — decode with a permissive fallback
    if isinstance(text, str):
        try:
            return text.decode("utf-8").strip()
        except (UnicodeDecodeError, AttributeError):
            return text.decode("latin-1").strip()
    # Any other type (.NET object, int, etc.) — convert via unicode()
    try:
        return unicode(text).strip()
    except Exception:
        return u"Unnamed"


def feet_to_meters(value):
    """Convert a length from Revit's internal units (decimal feet) to meters.

    Revit 2021+ exposes DB.UnitTypeId.Meters; older versions only have the
    deprecated DB.DisplayUnitType.DUT_METERS. Try the modern API first and
    fall back for compatibility with pre-2021 Revit.
    """
    try:
        return DB.UnitUtils.ConvertFromInternalUnits(value, DB.UnitTypeId.Meters)
    except AttributeError:
        return DB.UnitUtils.ConvertFromInternalUnits(value, DB.DisplayUnitType.DUT_METERS)


def to_param_string(value):
    """Safely coerce a value for a String-storage Revit parameter.

    Calling str() on a Python 2 unicode object that contains non-ASCII
    characters (e.g. Cyrillic) corrupts it under IronPython 2 - the string
    encoder in place_family/modify_element/creation callers must pass
    unicode straight through instead of re-wrapping it in str().
    """
    if isinstance(value, unicode):
        return value
    if isinstance(value, str):
        return normalize_string(value)
    return unicode(value)


def fix_mojibake(value):
    """Reverse UTF-8-bytes-decoded-as-Latin-1 corruption in incoming POST
    JSON bodies.

    Confirmed via diagnostics: this pyRevit Routes install's HTTP/JSON layer
    decodes the request body as Latin-1 instead of UTF-8, so every non-ASCII
    UTF-8 byte pair becomes two separate Latin-1 codepoints (e.g. Cyrillic
    "Т" sent as bytes D0 A2 arrives as the two characters U+00D0 U+00A2
    instead of U+0422). Re-encoding as Latin-1 recovers the original UTF-8
    bytes, which can then be decoded correctly. This is a safe no-op for
    plain ASCII text and falls back to the original value if the round-trip
    isn't valid (i.e. the text wasn't actually mangled this way).
    """
    if not isinstance(value, unicode):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def fix_mojibake_deep(value):
    """Recursively apply fix_mojibake() through a parsed JSON structure."""
    if isinstance(value, dict):
        return dict(
            (fix_mojibake_deep(k), fix_mojibake_deep(v)) for k, v in value.items()
        )
    if isinstance(value, list):
        return [fix_mojibake_deep(v) for v in value]
    if isinstance(value, unicode):
        return fix_mojibake(value)
    return value


def meters_to_feet(value):
    """Convert a length from meters to Revit's internal units (decimal feet).

    Counterpart to feet_to_meters, used when accepting coordinates/dimensions
    from callers who think in meters (matches the architect-focused units
    already used elsewhere, e.g. list_levels).
    """
    try:
        return DB.UnitUtils.ConvertToInternalUnits(value, DB.UnitTypeId.Meters)
    except AttributeError:
        return DB.UnitUtils.ConvertToInternalUnits(value, DB.DisplayUnitType.DUT_METERS)


def make_element_id(value):
    """Build a DB.ElementId from a plain int/long, tolerant of Revit's API
    version differences.

    Newer Revit API (2024+) gives ElementId multiple constructor overloads
    (BuiltInParameter, BuiltInCategory, Int64), and IronPython can't always
    resolve a bare Python int against them ("Multiple targets could match").
    Casting explicitly to System.Int64 disambiguates it; older API versions
    accept the same cast without issue.
    """
    return DB.ElementId(System.Int64(int(value)))


def element_id_value(element_id):
    """Get the integer value from an ElementId.

    Revit 2025+ uses .Value (int64), older versions use .IntegerValue (int32).
    Revit 2026 removed .IntegerValue entirely.
    """
    try:
        return int(element_id.Value)
    except AttributeError:
        return int(element_id.IntegerValue)


def get_element_name(element):
    """
    Get the name of a Revit element.
    Useful for both FamilySymbol and other elements.
    """
    try:
        return element.Name
    except AttributeError:
        return DB.Element.Name.__get__(element)


def find_family_symbol_safely(doc, target_family_name, target_type_name=None):
    """
    Safely find a family symbol by name
    """
    try:
        collector = DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol)

        for symbol in collector:
            if symbol.Family.Name == target_family_name:
                if not target_type_name or symbol.Name == target_type_name:
                    return symbol
        return None
    except Exception as e:
        logger.error("Error finding family symbol: %s", str(e))
        return None
