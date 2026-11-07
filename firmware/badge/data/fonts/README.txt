Schedule fonts

Opt-in use
----------
Set "smooth_text": true in the existing /data/panel_config.json, preserving
its other settings. The default is false. Schedule apps read the setting each
time they open, and only enable it on a supported B1 panel. With the setting
off, or on an 84 panel, they use the original white background, built-in fonts,
and ordinary display update.

With smooth text enabled, the 14px and 16px fonts below are loaded lazily.
Missing or unreadable font files fall back to the corresponding built-in font.

Other apps can use lib.smooth_text.enabled(display, config=None), returning
a boolean, and load_font(filename, fallback). A font
filename can be a basename under /data/fonts or an absolute path. Successful
loads are cached; both the font and its backing storage remain alive. The
native binfont_create_from_buffer loader is preferred, with the upstream
lib.binfont loader available on firmware without the native loader. Both can
fall back to the supplied built-in font.

Font loading does not change the display mode. Apps opt into gray rendering
using display.refresh_lvgl(grayscale=True) and display.update(grayscale=True).
For a scene that also contains red, use mixed=True consistently in draw_png,
refresh_lvgl and update instead. Ordinary calls keep their stock behavior.

Font profiles and reproduction
-----------------------------
These are Montserrat Medium v7.200, generated at their native pixel sizes with
lv_font_conv 1.5.3, strong auto-hinting, and uncompressed 4-bit coverage.
The SIL Open Font License and original copyright notice are in OFL.txt.

montserrat-14-soft.bin: strong hinting with expanded faint edge coverage.
Only stored coverage level 3/15 is promoted to 4/15. With the driver's gray
mapping, 197 faint pixels across the font become gray. Black cores,
h/k/n/m/l straight stems, glyph bounds, advances, and kerning remain unchanged.

montserrat-16-strong.bin: strong auto-hinting with unchanged coverage.
The 24-pixel schedule font remains the built-in Montserrat font.

Both files cover ASCII, degree, and bullet. They cover every printable
character in the checked-in schedule.csv; FontAwesome icons are not included.
Line height/base line match the built-ins: 16/3 at 14px and 18/3 at 16px.
Only the generated ascent metadata is adjusted to preserve those metrics.

Source:
https://raw.githubusercontent.com/lvgl/lvgl/v9.2.2/scripts/built_in_font/Montserrat-Medium.ttf
TTF SHA256:
421f26b23e2be6b98373d32acd3cb2897b154d4bf0a77d26534ce476e4cbed53

Asset SHA256:
montserrat-14-soft.bin
6082b07dbe2d5a86261e4d96c28280c6ebba9d11e0162cd1f70e805615463b20
montserrat-16-strong.bin
b4a9368e4dda06a630e13ca1c43545676a66f16d633de1bec243e04ddb5035f0

Reproduce from the repository root; dependencies stay outside the repository:

  font_build_dir=$(mktemp -d)
  npm install --prefix "$font_build_dir" --ignore-scripts --no-audit --no-fund lv_font_conv@1.5.3
  curl -fL 'https://raw.githubusercontent.com/lvgl/lvgl/v9.2.2/scripts/built_in_font/Montserrat-Medium.ttf' -o "$font_build_dir/Montserrat-Medium.ttf"
  node firmware/tools/build_schedule_fonts.js "$font_build_dir/node_modules/lv_font_conv" "$font_build_dir/Montserrat-Medium.ttf"

The generator checks the converter version, source hash, and expected output
hashes before writing either font. Its optional third argument selects a
different output directory.
