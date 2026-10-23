"""Capture existing numeric firmware diagnostics and safe ELF identity; never raw serial lines."""
from pathlib import Path
import collections
import json
import re
import sys
import time
from sanitize_serial_stream import sanitize, ANSI

ROOT = Path(__file__).resolve().parent
ELF = 'f81182f9b2baddf9728a0a4e734c4512df348c95bdcd02d36119817eedb359f5'
PATTERNS = [
 ('status',r'pocket_status: target=(\d+) http=(-?\d+) backend_available=([01]) stt=([01])\s*$',('target','http_status','backend_available','stt')),
 ('view',r'pocket_view: target=(\d+) wifi=([01]) online=([01]) speech_ready=([01])\s*$',('target','wifi','online','speech_ready')),
 ('funnel_status',r'pocket_funnel_status: approved_origin=([01]) public_ca=([01]) http=(-?\d+)\s*$',('approved_origin','public_ca','http_status')),
 ('delivery',r'pocket_delivery: target=(\d+) text_pages=([01]) speech_segments=([01])\s*$',('target','text_pages','speech_segments')),
 ('link_mode',r'pocket_link_mode: target=(\d+) approved_origin=([01]) public_ca=([01]) legacy_preserved=([01])\s*$',('target','approved_origin','public_ca','legacy_preserved')),
 ('migration',r'pocket_link_migration: result=(-?\d+) preserved=([01]) wifi=([01]) windows=([01]) pairing=([01]) ca=([01])\s*$',('result','preserved','wifi','windows','pairing','ca')),
 ('heap',r'pocket_tls_heap: target=(\d+) http=(-?\d+) free_before=(\d+) largest_before=(\d+) free_after=(\d+) largest_after=(\d+)\s*$',('target','http_status','free_before','largest_before','free_after','largest_after')),
 ('voice_request',r'pocket_voice_request: target=(\d+) stage=(\d+) error=(-?\d+) http=(-?\d+) duration_ms=(\d+) heap=([01]) free=(\d+) largest=(\d+)\s*$',('target','stage','error','http_status','duration_ms','heap','free','largest')),
 ('clock',r'pocket_clock: ready=([01]) offset_minutes=(-?\d+)\s*$',('ready','offset_minutes')),
 ('poll_delay',r'pocket_poll: delay_ms=(\d+)\s*$',('delay_ms',)),
 ('screen_power',r'pocket_power: screen_off=([01])\s*$',('screen_off',)),
]
PATTERNS = [(kind, re.compile(pattern), keys) for kind, pattern, keys in PATTERNS]
TLS = re.compile(r'pocket_tls: target=(\d+) http=(-?\d+) tls=(0x[0-9a-f]+) mbedtls=(0x[0-9a-f]+) verify=(0x[0-9a-f]+) ca=([01]) utc_ready=([01])(?: free_before=(\d+) largest_before=(\d+) free_after=(\d+) largest_after=(\d+))?(?: heap_before=([01]))?\s*$', re.I)

def main():
    started = time.time(); pending = ''; events = []; counts = collections.Counter(); lines = 0
    def consume(line):
        nonlocal lines
        lines += 1
        line = ANSI.sub('', line)[:8192]
        rows = []
        for kind, pattern, keys in PATTERNS:
            found = pattern.search(line)
            if found:
                rows.append({'kind': kind, **dict(zip(keys, map(int, found.groups())))})
        found = TLS.search(line)
        if found:
            target, http, tls, mbed, verify, ca, utc, *_ = found.groups()
            rows.append({'kind':'target_tls','target':int(target),'http_status':int(http),'tls_code':int(tls,16),'mbedtls_code':int(mbed,16),'verify_flags':int(verify,16),'custom_ca':int(ca),'utc_ready':int(utc)})
        found = re.search(r'ELF file SHA256:\s*([0-9a-f]{8,64})', line, re.I)
        if found:
            value = found.group(1).lower()
            rows.append({'kind':'application_elf_identity','observed_hex_prefix':value,'observed_prefix_length':len(value),'matches_last_delivered_elf':ELF.startswith(value),'last_delivered_elf_sha256':ELF,'fresh_full_binary_readback_claimed':False})
        if 'Project name:' in line:
            rows.append({'kind':'application_project','matches_expected_hermes_pocket':'hermes-pocket' in line.lower()})
        found = re.search(r'App version:\s*([vV]?\d+(?:\.\d+){0,3}(?:[-+][A-Za-z0-9_.-]{1,48})?)\b', line)
        if found:
            rows.append({'kind':'application_version','version':found.group(1)})
        if not rows:
            rows, category = sanitize(line)
            counts[category] += 1
            rows = [row for row in rows if row['kind'] in ('wifi','boot_seen','runtime_build_version','server_origin','connecting_marker')]
        for row in rows:
            counts[row['kind']] += 1
            if len(events) < 400:
                events.append({'observed_at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),**row})
    while True:
        chunk = sys.stdin.read(1024)
        if not chunk:
            break
        pending += chunk
        while '\n' in pending or '\r' in pending:
            found = re.search(r'[\r\n]', pending)
            consume(pending[:found.start()]); pending = pending[found.end():]
        if len(pending) > 8192:
            counts['oversized_fragment_suppressed'] += 1; pending = ''
    if pending:
        consume(pending)
    result = {'ok':True,'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(started)),'finished_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'lines_processed':lines,'counts':dict(counts),'events':events,'raw_serial_logs_saved_or_output':False,'credentials_audio_chat_saved_or_output':False}
    with (ROOT / 'serial-connecting-state-sanitized.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2,sort_keys=True)
    print(json.dumps(result,sort_keys=True),flush=True)

if __name__ == '__main__':
    main()
