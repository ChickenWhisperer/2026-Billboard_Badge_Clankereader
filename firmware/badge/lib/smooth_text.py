"""Opt-in smooth text and reusable, lazy LVGL binary-font loading.

``enabled(display)`` reads the existing panel_config.json setting.
``smooth_text`` defaults to false and requires a B1 panel. Apps can supply
an already-loaded config dictionary instead.

``load_font(filename, fallback)`` loads only when called, caches successful
loads, and keeps native font storage alive. It does not enable gray rendering;
apps must explicitly request the matching display refresh mode.
"""

import lvgl

from lib import json_configs


_LOADED_FONTS = {}


def enabled(display, config=None):
    """Return whether smooth text was explicitly enabled on a B1 panel."""
    if config is None:
        try:
            config = json_configs.load()
        except (OSError, ValueError):
            config = {}
    if not isinstance(config, dict):
        config = {}
    return (config.get("smooth_text") is True
            and bool(getattr(display, "supports_grayscale", False)))


def load_font(filename, fallback):
    """Load a font basename from /data/fonts (or an absolute path), or fallback.

    Prefer the native loader. Older firmware can use the upstream binfont
    module, which owns its native backing buffers in its lifetime registry.
    Failed loads are not cached, so installing an optional font takes effect
    the next time its app opens. Neither loader is imported/called implicitly.
    """
    path = filename if filename.startswith("/") else "/data/fonts/" + filename
    cached = _LOADED_FONTS.get(path)
    if cached is not None:
        return cached[0]

    native = getattr(lvgl, "binfont_create_from_buffer", None)
    if native is not None:
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except (OSError, MemoryError):
            print("Could not load %s; using built-in font" % filename)
            return fallback
        try:
            font = native(data, len(data))
        except Exception:
            font = None
        if font is not None:
            # Retain bytes as well as the font even on bindings that copy them.
            _LOADED_FONTS[path] = (font, data)
            return font
        data = None

    try:
        from lib.binfont import load_binfont
        font = load_binfont(path, ref_font=fallback)
        if font is not None:
            _LOADED_FONTS[path] = (font, None)
            return font
    except Exception:
        pass
    print("Could not load %s; using built-in font" % filename)
    return fallback
