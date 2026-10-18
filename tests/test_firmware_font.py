"""Pocket CJK coverage and real LVGL lookup checks; no device access."""
import os
import hashlib
import importlib.util
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FONT_DIR = ROOT / 'firmware/fonts'
FIXTURES = '轉錄覆耗慢丟棄转录覆耗慢丢弃，。！？：；、（）「」『』【】《》…—·'
PUNCTUATION = '　、。〃々〆〇〈〉《》「」『』【】〒〔〕〖〗〘〙〚〛〜〝〞〟〰〽・！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～｟｠｡｢｣､･·—–‘’“”…'


def lvgl_directory():
    candidates = [os.environ.get('LVGL_DIR'),
                  ROOT / 'firmware/cached_components/lvgl',
                  ROOT.parent / 'HPusage-build-20261015-001/firmware/cached_components/lvgl']
    return next((pathlib.Path(path) for path in candidates
                 if path and (pathlib.Path(path) / 'lvgl.h').is_file()), None)


def chinese_sets():
    gb2312 = set()
    big5 = set()
    for high in range(0xB0, 0xF8):
        for low in range(0xA1, 0xFF):
            try:
                character = bytes((high, low)).decode('gb2312')
            except UnicodeDecodeError:
                continue
            if 0x4E00 <= ord(character) <= 0x9FFF:
                gb2312.add(ord(character))
    for high in range(0xA4, 0xC7):
        for low in (*range(0x40, 0x7F), *range(0xA1, 0xFF)):
            if (high, low) > (0xC6, 0x7E):
                continue
            character = bytes((high, low)).decode('big5')
            if 0x4E00 <= ord(character) <= 0x9FFF:
                big5.add(ord(character))
    return gb2312, big5


def annotated_codepoints(path):
    if not path.is_file():
        return set()
    return {int(value, 16) for value in
            re.findall(r'/\* U\+([0-9A-Fa-f]+)', path.read_text(encoding='utf-8'))}


class FirmwareFontTests(unittest.TestCase):
    def test_required_chinese_and_punctuation_coverage(self):
        lvgl = lvgl_directory()
        if lvgl:
            baseline = annotated_codepoints(lvgl / 'src/font/lv_font_source_han_sans_sc_16_cjk.c')
        else:
            manifest = json.loads((FONT_DIR / 'pocket_cjk_16.manifest.json').read_text(encoding='utf-8'))
            baseline = {int(point, 16) for point in manifest['baseline']['codepoints']}
        self.assertEqual(len(baseline), 1433)
        gb2312, big5 = chinese_sets()
        self.assertEqual(len(gb2312), 6763)
        self.assertEqual(len(big5), 5401)
        self.assertEqual(len(gb2312 | big5), 8836)
        fallback = annotated_codepoints(FONT_DIR / 'lv_font_pocket_cjk_16.c')
        required = gb2312 | big5 | baseline | set(map(ord, PUNCTUATION))
        missing = required - baseline - fallback
        self.assertEqual(len(missing), 0, f'{len(missing)} missing glyphs; first: ' +
                         ', '.join(f'U+{point:04X}' for point in sorted(missing)[:20]))
        self.assertFalse(set(map(ord, FIXTURES)) - baseline - fallback)

    def test_manifest_and_whitelist_match_actual_artifact(self):
        manifest = json.loads((FONT_DIR / 'pocket_cjk_16.manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['font']['name'], 'Pocket CJK')
        self.assertEqual(manifest['font']['bits_per_pixel'], 2)
        self.assertEqual(manifest['font']['size_pixels'], 16)
        self.assertEqual(manifest['source']['sha256'],
                         '10e6d832bc73650840aa7fbfec4e10c527f8136ae2aec71c3e1c13a67475c24a')
        self.assertEqual(manifest['converter']['version'], '1.5.3')
        gb2312, big5 = chinese_sets()
        baseline = {int(point, 16) for point in manifest['baseline']['codepoints']}
        expected = gb2312 | big5 | baseline | set(map(ord, PUNCTUATION))
        required = {int(line[2:], 16) for line in
                    (FONT_DIR / 'pocket_cjk_16.target.txt').read_text(encoding='utf-8').splitlines()}
        self.assertEqual(required, expected)
        generated = annotated_codepoints(FONT_DIR / 'lv_font_pocket_cjk_16.c')
        self.assertEqual(generated, required - baseline)
        self.assertEqual(manifest['coverage']['generated_count'], len(generated))
        self.assertEqual(manifest['coverage']['target_count'], len(required))
        self.assertEqual(manifest['coverage']['missing_source_codepoints'], [])
        for filename, info in manifest['artifacts'].items():
            data = (FONT_DIR / filename).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), info['sha256'], filename)
            self.assertEqual(len(data), info['bytes'], filename)
        self.assertIn("Reserved Font", (FONT_DIR / 'OFL.txt').read_text(encoding='utf-8'))

    def test_generator_refuses_missing_source_glyphs(self):
        path = ROOT / 'tools/generate_pocket_font.py'
        spec = importlib.util.spec_from_file_location('generate_pocket_font', path)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        with self.assertRaisesRegex(ValueError, 'U\\+10FFFF'):
            generator.require_source_glyphs({ord('轉'), 0x10FFFF}, {ord('轉')})

    def test_real_lvgl_lookup_and_bitmap_data(self):
        lvgl = lvgl_directory()
        compiler = os.environ.get('POCKET_HOST_CC') or shutil.which('clang') or shutil.which('gcc')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler or not lvgl:
            self.skipTest('host C compiler or LVGL_DIR (LVGL 9.5 source) unavailable')
        self.assertTrue((FONT_DIR / 'lv_font_pocket_cjk_16.c').is_file())
        target = [int(line[2:], 16) for line in
                  (FONT_DIR / 'pocket_cjk_16.target.txt').read_text(encoding='utf-8').splitlines()]
        generated_text = (FONT_DIR / 'lv_font_pocket_cjk_16.c').read_text(encoding='utf-8')
        unicode_arrays = re.findall(r'static const uint16_t (unicode_list_\d+)\[\]', generated_text)
        offset_arrays = re.findall(r'static const uint8_t (glyph_id_ofs_list_\d+)\[\]', generated_text)
        extra_sizes = ''.join(' + sizeof(' + name + ')' for name in unicode_arrays)
        offset_sizes = ''.join(' + sizeof(' + name + ')' for name in offset_arrays)
        harness = r'''
#include "lvgl.h"
#include "src/misc/lv_text_private.h"
#include "src/core/lv_global.h"
#include "src/stdlib/lv_mem.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "@FONT@"
/* Initialize the real allocator for the font component, without widgets. */
lv_global_t lv_global;
/* Unused helpers in LVGL's shared C files reference other subsystems.
 * These boundaries fail immediately if this font check ever reaches them.
 * Font lookup, bitmap lookup, UTF-8, binary search and allocation remain real. */
lv_fs_res_t lv_fs_open(lv_fs_file_t * file, const char * path, lv_fs_mode_t mode) { abort(); }
lv_fs_res_t lv_fs_close(lv_fs_file_t * file) { abort(); }
lv_fs_res_t lv_fs_write(lv_fs_file_t * file, const void * data, uint32_t size, uint32_t * written) { abort(); }
void lv_image_cache_drop(const void * source) { abort(); }
lv_result_t lv_draw_buf_convert_premultiply(lv_draw_buf_t * buffer) { abort(); }
static const uint32_t target[] = {@TARGET@};
#define CHECK(c,m) do { if(!(c)) { printf("FAIL U+%04X: %s\n", (unsigned)point, m); return 1; } } while(0)
int main(void) {
    lv_mem_init();
    lv_mem_monitor_t before, after;
    lv_mem_monitor(&before);
    lv_font_t font = lv_font_source_han_sans_sc_16_cjk;
    font.fallback = &lv_font_pocket_cjk_16;
    uint32_t point = 0;
    lv_font_glyph_dsc_t glyph, primary;
    for(size_t i = 0; i < sizeof(target)/sizeof(target[0]); i++) {
        point = target[i];
        CHECK(lv_font_get_glyph_dsc(&font, &glyph, point, 0), "missing target glyph");
        CHECK(!glyph.is_placeholder && glyph.resolved_font, "placeholder target glyph");
        if(lv_font_get_glyph_dsc(&lv_font_source_han_sans_sc_16_cjk, &primary, point, 0)) {
            CHECK(glyph.resolved_font == &font, "retained primary was bypassed");
            CHECK(glyph.adv_w == primary.adv_w && glyph.box_w == primary.box_w &&
                  glyph.box_h == primary.box_h && glyph.ofs_x == primary.ofs_x &&
                  glyph.ofs_y == primary.ofs_y, "primary metrics changed");
        } else {
            CHECK(glyph.resolved_font == &lv_font_pocket_cjk_16, "fallback was not resolved");
            CHECK(glyph.format == LV_FONT_GLYPH_FORMAT_A2, "fallback is not 2bpp");
            const lv_font_fmt_txt_dsc_t * data = glyph.resolved_font->dsc;
            const lv_font_fmt_txt_glyph_dsc_t * d = &data->glyph_dsc[glyph.gid.index];
            size_t bytes = ((size_t)d->box_w * d->box_h * data->bpp + 7) / 8;
            CHECK(d->bitmap_index + bytes <= sizeof(glyph_bitmap), "bitmap exceeds array");
            if(point != 0x3000) {
                CHECK(d->box_w && d->box_h && bytes, "blank fallback glyph");
                glyph.req_raw_bitmap = 1;
                const uint8_t * bitmap = lv_font_get_bitmap_fmt_txt(&glyph, NULL);
                CHECK(bitmap, "bitmap lookup failed");
                bool ink = false;
                for(size_t j = 0; j < bytes; j++) ink |= bitmap[j] != 0;
                CHECK(ink, "empty fallback bitmap");
            }
        }
    }
    const char * fixtures = "@FIXTURES@";
    uint32_t cursor = 0;
    while(fixtures[cursor]) {
        point = lv_text_encoded_next(fixtures, &cursor);
        CHECK(lv_font_get_glyph_dsc(&font, &glyph, point, 0) && !glyph.is_placeholder,
              "literal Traditional/Simplified fixture did not resolve");
    }
    point = 0x10FFFF;
    CHECK(!lv_font_get_glyph_dsc(&font, &glyph, point, 0), "unsupported glyph silently resolved");
    lv_mem_monitor(&after);
    CHECK(before.free_size == after.free_size, "font lookup allocated persistent memory");
    CHECK(LV_MEM_SIZE == 24U * 1024U, "host font check must retain 24 KB LVGL pool");
    printf("Pocket CJK lookup: PASS (%zu target codepoints)\n", sizeof(target)/sizeof(target[0]));
    printf("Pocket CJK host array bytes: bitmap=%zu glyph_dsc=%zu cmap=%zu unicode=%zu offsets=%zu font_dsc=%zu font=%zu\n",
           sizeof(glyph_bitmap), sizeof(glyph_dsc), sizeof(cmaps), (size_t)0@EXTRA@, (size_t)0@OFFSETS@,
           sizeof(font_dsc), sizeof(lv_font_pocket_cjk_16));
    return 0;
}
'''
        harness = harness.replace('@FONT@', str(FONT_DIR / 'lv_font_pocket_cjk_16.c').replace('\\', '/'))
        harness = harness.replace('@TARGET@', ','.join(hex(point) for point in target))
        harness = harness.replace('@FIXTURES@', FIXTURES).replace('@EXTRA@', extra_sizes)
        harness = harness.replace('@OFFSETS@', offset_sizes)
        # Windows child compiler processes cannot share AppContainer-private
        # temporary paths; keep transient build files in the shared workspace,
        # outside the repository so source manifests never capture binaries.
        with tempfile.TemporaryDirectory(prefix='pocket-font-test-', dir=ROOT.parent) as directory:
            temporary = pathlib.Path(directory)
            self.assertEqual(temporary.resolve().parent, ROOT.parent.resolve())
            source = temporary / 'font_lookup.c'
            source.write_text(harness, encoding='utf-8')
            binary = temporary / ('font_lookup.exe' if os.name == 'nt' else 'font_lookup')
            include = ['-DLV_CONF_INCLUDE_SIMPLE', '-I' + str(ROOT / 'firmware/tests'),
                       '-I' + str(lvgl)]
            if os.name == 'nt':
                include.append('-D_CRT_SECURE_NO_WARNINGS')
            # Real font, search, UTF-8, bitmap and allocator implementations.
            # Function sections discard unrelated public functions in these
            # LVGL files; UI rendering is covered by the snapshot suite.
            dependencies = ['font/lv_font.c', 'font/fmt_txt/lv_font_fmt_txt.c',
                            'font/lv_font_source_han_sans_sc_16_cjk.c', 'font/lv_font_montserrat_14.c',
                            'draw/lv_draw_buf.c', 'misc/lv_utils.c', 'misc/lv_text.c',
                            'misc/lv_ll.c', 'misc/lv_log.c', 'misc/lv_color.c',
                            'misc/lv_area.c', 'misc/lv_math.c', 'misc/lv_color_op.c',
                            'tick/lv_tick.c', 'stdlib/lv_mem.c',
                            'stdlib/builtin/lv_mem_core_builtin.c',
                            'stdlib/builtin/lv_tlsf.c', 'stdlib/builtin/lv_string_builtin.c',
                            'stdlib/builtin/lv_sprintf_builtin.c']
            sources = [str(lvgl / 'src' / name) for name in dependencies]
            linker = ['-Wl,/OPT:REF'] if os.name == 'nt' else ['-Wl,--gc-sections', '-lm']
            arguments = ['-std=c11', '-O1', '-w', '-ffunction-sections', '-fdata-sections',
                         *include, str(source), *sources, *linker, '-o', str(binary)]
            response = temporary / 'font.rsp'
            response.write_text('\n'.join('"' + value.replace('\\', '/').replace('"', '\\"') + '"'
                                          for value in arguments), encoding='utf-8')
            built = subprocess.run([compiler, '@' + str(response)], cwd=directory,
                                   capture_output=True, text=True, timeout=240)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn('Pocket CJK lookup: PASS', run.stdout)
            print(run.stdout.strip())


if __name__ == '__main__':
    unittest.main()
