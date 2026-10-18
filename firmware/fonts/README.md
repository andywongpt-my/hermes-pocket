# Pocket CJK

Pocket CJK is the 16 px, uncompressed 2 bpp Chinese fallback for the retained
`lv_font_source_han_sans_sc_16_cjk` primary. Keep the primary font first so its
Latin, kana, icons, existing Chinese forms, kerning and layout remain intact.
The generated glyphs and descriptors are constant data under the firmware's
LVGL 9 build; this font adds no glyph cache or persistent allocation.

The font data is a derivative of Adobe Source Han Sans TC Regular,
Copyright 2014–2025 Adobe, distributed under SIL OFL 1.1 in [OFL.txt](OFL.txt).
Its derivative name is **Pocket CJK**. Adobe's reserved font name appears only
in attribution and the original source filename.

Coverage is explicit, rather than a broad CJK Unicode range:

* GB2312 level 1 and 2 Han: 6,763 distinct characters from byte rows B0–F7.
* Big5 common level 1 Han: 5,401 distinct characters from A440–C67E.
* Their union: 8,836 Han characters.
* All 1,433 original primary codepoints, including 83 Han outside that union.
* The 80 punctuation/spacing codepoints listed in the manifest.

The resulting union is **9,306 codepoints / 8,919 Han**. Only the **7,873**
codepoints absent from the primary are generated here. Existing private-use
icons stay in their original font; every newly generated codepoint is checked
against a nonzero glyph in the pinned Adobe OpenType cmap before conversion.
`pocket_cjk_16.target.txt` records the union and `pocket_cjk_16.additions.txt`
records the exact fallback subset, one sorted `U+XXXX` value per line.

The manifest records upstream SHA-256 and Git blob hashes, exact OFL hash,
the converter's npm integrity and installed tree hash, generator hash,
conversion options and artifact hashes. The upstream OTF and npm installation
are build inputs, not files included in this repository. The source URLs in
the manifest identify the official upstream downloads.

To reproduce, use Python 3.11+ and Node.js, install the pinned converter in a
separate tools directory with `npm install --prefix <tools> --ignore-scripts
--save-exact lv_font_conv@1.5.3`, then run this command from the repository root
with local input paths:

```text
python tools/generate_pocket_font.py --source <SourceHanSansTC-Regular.otf> --license <upstream-LICENSE.txt> --baseline-font <lvgl/src/font/lv_font_source_han_sans_sc_16_cjk.c> --converter <tools/node_modules/lv_font_conv/lv_font_conv.js>
```

Add `--check` to regenerate in temporary storage and compare every generated
artifact byte for byte. The generator performs no downloads or installations.
The conversion disables compression, prefiltering and kerning for the added
Chinese glyphs; the retained primary still supplies its original kerning.

Compile `lv_font_pocket_cjk_16.c` and include `lv_font_pocket_cjk_16.h`. Set the
fallback of an application-owned shallow copy of the primary font to
`&lv_font_pocket_cjk_16`; the original LVGL font is constant.

Run `python -m unittest discover -s tests -p test_firmware_font.py -v`.
With `LVGL_DIR` pointing to LVGL 9.5 sources and a host C compiler available,
the test executes actual LVGL lookup for every target codepoint, checks
primary metric preservation, verifies fallback bitmap bounds and ink, and
decodes literal Traditional/Simplified fixtures using LVGL's UTF-8 decoder.
The host test uses the same 24 KB LVGL pool configuration as UI snapshots.
Its font, bitmap, fallback, binary search, UTF-8 and TLSF implementations are
real LVGL code. Unused filesystem, image-cache and premultiply boundaries
abort if reached. UI rendering is verified by the separate snapshot suite.

The bitmap array is exactly **468,904 bytes**. The other data comprises 7,874
glyph descriptors, 35 cmaps, 6,888 16-bit Unicode offsets and 885 8-bit glyph
offsets, plus the font descriptors. The 3,872,832-byte C file is source text.
On the selected Windows Clang host, measured array/descriptor sizes total
547,781 bytes. A separately compiled COFF object is 549,998 bytes, with
547,952 bytes in `.rdata` (including alignment) and zero bytes in `.data`
and `.bss`. These host ABI/object measurements are not ESP flash sizes.
Use the target linker map and final application binary to measure flash use
and confirm the retained 3 MB application partition still fits.
