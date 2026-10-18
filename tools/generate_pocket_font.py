#!/usr/bin/env python3
"""Rebuild the local, OFL-licensed Pocket CJK LVGL fallback without downloads."""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_SHA256 = '10e6d832bc73650840aa7fbfec4e10c527f8136ae2aec71c3e1c13a67475c24a'
SOURCE_GIT_BLOB = 'd03916ca3b55e217194f7c35c1f5c1193c60b0a7'
LICENSE_SHA256 = 'fcac737e761ec63dbfbdce11030a1780161920d80315edba9c8beff1c2bac5a2'
BASELINE_SHA256 = '4caaa2aff00bb59d1155227c838ced106ab4d61717afcaa66328cb0b3136785b'
CONVERTER_VERSION = '1.5.3'
CONVERTER_INTEGRITY = 'sha512-0xJQThBOw2iptFccSXrKDIUTQAwr/2zhKjCI1lATIRgZo8uvYRTmenKafW9yTw6G0y5AyW00tqGpUtYuTuBIbQ=='
PUNCTUATION = '　、。〃々〆〇〈〉《》「」『』【】〒〔〕〖〗〘〙〚〛〜〝〞〟〰〽・！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝～｟｠｡｢｣､･·—–‘’“”…'
FONT_NAME = 'lv_font_pocket_cjk_16'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def checked_bytes(path, expected, description):
    data = path.read_bytes()
    actual = sha256(data)
    if actual != expected:
        raise ValueError(f'{description} SHA-256 mismatch: {actual}; expected {expected}')
    return data


def font_codepoints(data):
    """Read non-.notdef Unicode mappings from the pinned OpenType cmap format 12."""
    table_count = struct.unpack_from('>H', data, 4)[0]
    tables = {}
    for index in range(table_count):
        record = 12 + index * 16
        tag = data[record:record + 4]
        tables[tag] = struct.unpack_from('>II', data, record + 8)
    cmap_offset, cmap_length = tables[b'cmap']
    end = cmap_offset + cmap_length
    record_count = struct.unpack_from('>H', data, cmap_offset + 2)[0]
    points = set()
    seen = set()
    for index in range(record_count):
        platform, encoding, offset = struct.unpack_from('>HHI', data, cmap_offset + 4 + index * 8)
        if not (platform == 0 or (platform == 3 and encoding == 10)):
            continue
        subtable = cmap_offset + offset
        if subtable in seen or struct.unpack_from('>H', data, subtable)[0] != 12:
            continue
        seen.add(subtable)
        length, _, groups = struct.unpack_from('>III', data, subtable + 4)
        if subtable + length > end or 16 + groups * 12 > length:
            raise ValueError('malformed source cmap format 12')
        for group in range(groups):
            first, last, glyph_id = struct.unpack_from('>III', data, subtable + 16 + group * 12)
            if last < first or last > 0x10FFFF:
                raise ValueError('invalid source cmap range')
            points.update(range(first + (glyph_id == 0), last + 1))
    if not seen:
        raise ValueError('source font has no supported Unicode cmap format 12')
    return points


def require_source_glyphs(required, available):
    missing = sorted(required - available)
    if missing:
        listing = ', '.join(f'U+{point:04X}' for point in missing)
        raise ValueError(f'source font lacks {len(missing)} required glyphs: {listing}')


def baseline_codepoints(data):
    points = {int(value, 16) for value in
              re.findall(r'/\* U\+([0-9A-Fa-f]+)', data.decode('utf-8'))}
    if len(points) != 1433:
        raise ValueError('unexpected retained baseline glyph count')
    return points


def chinese_repertoire():
    """GB2312 Han rows 16-87 and Big5 common level 1 (A440-C67E)."""
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
    if (len(gb2312), len(big5), len(gb2312 | big5)) != (6763, 5401, 8836):
        raise ValueError('Python codec repertoire differs from the reviewed Chinese sets')
    return gb2312, big5


def whitelist(points):
    return ''.join(f'U+{point:04X}\n' for point in sorted(points)).encode('ascii')


def numeric_array(text, name):
    match = re.search(r'\b' + re.escape(name) + r'\[\]\s*=\s*\{(.*?)\};', text, re.S)
    if not match:
        raise ValueError(f'converter omitted {name}')
    body = re.sub(r'/\*.*?\*/', '', match[1], flags=re.S)
    return [int(value, 0) for value in re.findall(r'0[xX][0-9A-Fa-f]+|\b\d+\b', body)]


def converter_metadata(converter):
    package = converter.parent
    metadata = json.loads((package / 'package.json').read_text(encoding='utf-8'))
    lock_path = package.parent.parent / 'package-lock.json'
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    locked = lock['packages']['node_modules/lv_font_conv']
    if metadata['version'] != CONVERTER_VERSION or locked['version'] != CONVERTER_VERSION:
        raise ValueError('lv_font_conv must be exactly version ' + CONVERTER_VERSION)
    if locked['integrity'] != CONVERTER_INTEGRITY:
        raise ValueError('lv_font_conv npm integrity differs from the reviewed package')
    tree = hashlib.sha256()
    paths = sorted((path for path in package.rglob('*') if path.is_file()),
                   key=lambda path: path.relative_to(package).as_posix())
    for path in paths:
        relative = path.relative_to(package).as_posix().encode('utf-8')
        tree.update(relative + b'\0' + path.read_bytes() + b'\0')
    return {'package': 'lv_font_conv', 'version': CONVERTER_VERSION,
            'npm_integrity': CONVERTER_INTEGRITY, 'installed_tree_sha256': tree.hexdigest(),
            'options': ['--size', '16', '--bpp', '2', '--format', 'lvgl',
                        '--no-compress', '--no-prefilter', '--no-kerning',
                        '--lv-include', 'lvgl.h', '--lv-font-name', FONT_NAME,
                        '--font', '<pinned-source>', '--symbols', '<sorted-additions-whitelist>']}


def generate(arguments):
    source = checked_bytes(arguments.source, SOURCE_SHA256, 'Adobe upstream font')
    blob = hashlib.sha1(b'blob ' + str(len(source)).encode('ascii') + b'\0' + source).hexdigest()
    if blob != SOURCE_GIT_BLOB:
        raise ValueError('Adobe upstream font Git blob mismatch')
    license_data = checked_bytes(arguments.license, LICENSE_SHA256, 'OFL license')
    baseline_data = checked_bytes(arguments.baseline_font, BASELINE_SHA256, 'retained LVGL primary font')
    converter = converter_metadata(arguments.converter)
    baseline = baseline_codepoints(baseline_data)
    gb2312, big5 = chinese_repertoire()
    punctuation = set(map(ord, PUNCTUATION))
    target = gb2312 | big5 | baseline | punctuation
    additions = target - baseline
    available = font_codepoints(source)
    # Retained icons/kana/Latin keep their original primary font data.
    require_source_glyphs(additions, available)
    with tempfile.TemporaryDirectory(prefix='pocket-cjk-') as directory:
        output = pathlib.Path(directory) / (FONT_NAME + '.c')
        command = [arguments.node, str(arguments.converter), '--size', '16', '--bpp', '2',
                   '--format', 'lvgl', '--no-compress', '--no-prefilter', '--no-kerning',
                   '--lv-include', 'lvgl.h', '--lv-font-name', FONT_NAME,
                   '--font', str(arguments.source), '--symbols', ''.join(map(chr, sorted(additions))),
                   '--output', str(output)]
        converted = subprocess.run(command, capture_output=True, text=True,
                                   encoding='utf-8', timeout=300, check=False)
        if converted.returncode:
            raise ValueError('lv_font_conv failed:\n' + converted.stdout + converted.stderr)
        text = output.read_text(encoding='utf-8')
    actual = {int(value, 16) for value in re.findall(r'/\* U\+([0-9A-Fa-f]+)', text)}
    if actual != additions:
        raise ValueError('converter output coverage differs from the additions whitelist')
    text = re.sub(r'\A/\*.*?\*/', '''/*
 * Pocket CJK, 16 px, 2 bpp. Generated by tools/generate_pocket_font.py.
 * Derivative of Adobe Source Han Sans TC Regular, Copyright 2014-2025 Adobe.
 * Font data licensed under SIL Open Font License 1.1; see OFL.txt.
 * Primary derivative name: Pocket CJK. See pocket_cjk_16.manifest.json for
 * pinned source/tool hashes, explicit whitelists and conversion options.
 */''', text, count=1, flags=re.S)
    if '.bpp = 2' not in text or '.bitmap_format = 0' not in text:
        raise ValueError('converter output is not uncompressed 2bpp')
    bitmap_bytes = len(numeric_array(text, 'glyph_bitmap'))
    descriptors = re.search(r'\bglyph_dsc\[\]\s*=\s*\{(.*?)\};', text, re.S)[1]
    arrays = re.findall(r'static const uint16_t (unicode_list_\d+)\[\]', text)
    offsets = re.findall(r'static const uint8_t (glyph_id_ofs_list_\d+)\[\]', text)
    cmap_count = len(re.findall(r'\.range_start\s*=', text))
    header = '''/* Pocket CJK font data is licensed under SIL OFL 1.1; see OFL.txt. */
#ifndef LV_FONT_POCKET_CJK_16_H
#define LV_FONT_POCKET_CJK_16_H
#include "lvgl.h"
#ifdef __cplusplus
extern "C" {
#endif
extern const lv_font_t lv_font_pocket_cjk_16;
#ifdef __cplusplus
}
#endif
#endif
'''
    files = {FONT_NAME + '.c': text.encode('utf-8'), FONT_NAME + '.h': header.encode('ascii'),
             'OFL.txt': license_data, 'pocket_cjk_16.target.txt': whitelist(target),
             'pocket_cjk_16.additions.txt': whitelist(additions)}
    manifest = {
        'schema_version': 1,
        'font': {'name': 'Pocket CJK', 'c_symbol': FONT_NAME, 'size_pixels': 16,
                 'bits_per_pixel': 2, 'compression': 'none', 'kerning': 'none'},
        'source': {'filename': 'SourceHanSansTC-Regular.otf', 'bytes': len(source),
                   'sha256': SOURCE_SHA256, 'git_blob_sha1': SOURCE_GIT_BLOB,
                   'url': 'https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/OTF/TraditionalChinese/SourceHanSansTC-Regular.otf',
                   'license_url': 'https://raw.githubusercontent.com/adobe-fonts/source-han-sans/release/LICENSE.txt',
                   'license_sha256': LICENSE_SHA256},
        'converter': converter,
        'generator_sha256': sha256(pathlib.Path(__file__).read_bytes()),
        'baseline': {'font': 'lv_font_source_han_sans_sc_16_cjk', 'sha256': BASELINE_SHA256,
                     'count': len(baseline), 'codepoints': [f'{point:04X}' for point in sorted(baseline)]},
        'coverage': {'gb2312_han_count': len(gb2312), 'big5_common_level1_han_count': len(big5),
                     'chinese_union_han_count': len(gb2312 | big5),
                     'punctuation': [f'{point:04X}' for point in sorted(punctuation)],
                     'punctuation_count': len(punctuation), 'target_count': len(target),
                     'target_han_count': sum(0x4E00 <= point <= 0x9FFF for point in target),
                     'generated_count': len(additions), 'missing_source_codepoints': [],
                     'source_verification': 'Every generated fallback codepoint has a nonzero source cmap glyph; retained primary codepoints keep their original font data.'},
        'arrays': {'glyph_bitmap_bytes': bitmap_bytes,
                   'glyph_descriptor_entries': len(re.findall(r'\.bitmap_index\s*=', descriptors)),
                   'cmap_entries': cmap_count,
                   'unicode_list_entries': sum(len(numeric_array(text, name)) for name in arrays),
                   'glyph_id_offset_bytes': sum(len(numeric_array(text, name)) for name in offsets),
                   'size_note': 'Bitmap byte count is exact. Descriptor/cmap ABI sizes and total linked firmware size must be measured in the target build.'},
        'artifacts': {name: {'bytes': len(data), 'sha256': sha256(data)} for name, data in sorted(files.items())},
    }
    files['pocket_cjk_16.manifest.json'] = (json.dumps(manifest, ensure_ascii=True, indent=2) + '\n').encode('utf-8')
    if arguments.check:
        mismatches = [name for name, data in files.items()
                      if not (arguments.output_dir / name).is_file() or
                      (arguments.output_dir / name).read_bytes() != data]
        if mismatches:
            raise ValueError('regenerated artifacts differ: ' + ', '.join(mismatches))
    else:
        arguments.output_dir.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            destination = arguments.output_dir / name
            if not destination.is_file() or destination.read_bytes() != data:
                destination.write_bytes(data)
    print(json.dumps({'mode': 'verified' if arguments.check else 'generated',
                      'coverage': manifest['coverage'], 'arrays': manifest['arrays'],
                      'c_source_bytes': len(files[FONT_NAME + '.c'])}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=pathlib.Path)
    parser.add_argument('--license', required=True, type=pathlib.Path)
    parser.add_argument('--baseline-font', required=True, type=pathlib.Path)
    parser.add_argument('--converter', required=True, type=pathlib.Path)
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--output-dir', type=pathlib.Path, default=ROOT / 'firmware/fonts')
    parser.add_argument('--check', action='store_true', help='regenerate into temporary storage and compare bytes')
    arguments = parser.parse_args()
    if not arguments.node:
        parser.error('Node.js is required; pass --node or put node on PATH')
    try:
        generate(arguments)
    except (ValueError, OSError, KeyError, struct.error, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
