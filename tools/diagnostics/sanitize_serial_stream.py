"""Filter serial input in memory; emit only typed connection diagnostics, never raw lines."""
from pathlib import Path
import collections
import ipaddress
import json
import re
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
SECRET = re.compile(r'(?i)(token|password|passwd|passphrase|authorization|bearer|api[_ -]?key|secret|\bpsk\b|\bssid\b)')
ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
SAFE_HOST = re.compile(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?')

def origin(text):
    try:
        parsed = urlsplit(text if '://' in text else 'https://' + text)
        host = parsed.hostname
        if not host or not SAFE_HOST.fullmatch(host):
            return None
        if any(len(part) >= 40 for part in host.split('.')):
            return None
        return parsed.scheme + '://' + host + (':' + str(parsed.port) if parsed.port else '')
    except ValueError:
        return None

def sanitize(line):
    line = ANSI.sub('', line)[:8192]
    low = line.lower()
    if SECRET.search(line):
        return [], 'sensitive_line_suppressed'
    events = []
    if any(word in low for word in ('wifi', 'wi-fi', 'wlan', 'got_ip', 'got ip', 'ip address', 'sta ip', 'esp_netif')):
        states = [state for state in ('disconnected', 'disconnect', 'connecting', 'connected', 'got_ip', 'got ip', 'failed', 'failure', 'reconnect', 'auth', 'assoc', 'init', 'run') if re.search(r'\b' + re.escape(state) + r'\b', low)]
        item = {'kind': 'wifi', 'states': states}
        addresses = []
        for value in re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', line):
            try:
                addresses.append(str(ipaddress.IPv4Address(value)))
            except ValueError:
                pass
        if addresses:
            item['ipv4'] = sorted(set(addresses))
        reasons = re.findall(r'(?i)\b(?:reason|errno|err|error|status)(?:[_ ]?code)?\s*[:=]\s*(-?(?:0x[0-9a-f]{1,8}|\d{1,8}))\b', line)
        if reasons:
            item['numeric_codes'] = reasons[:8]
        codes = re.findall(r'\b(?:WIFI_REASON_[A-Z0-9_]{1,64}|ESP_ERR_[A-Z0-9_]{1,64})\b', line)
        if codes:
            item['named_codes'] = sorted(set(codes))[:8]
        events.append(item)
    addresses = []
    for value in re.findall(r'https?://[^\s<>"\x27]+', line, flags=re.I):
        item = origin(value.rstrip('.,);]'))
        if item:
            addresses.append(item)
    if any(word in low for word in ('server', 'host', 'endpoint', 'base_url', 'bridge')):
        match = re.search(r'(?i)\b(?:server(?:[_ ](?:url|address))?|hostname|host|endpoint|base_url|bridge)\s*[:=]\s*["\x27]?([A-Za-z0-9_.:-]{3,260})', line)
        if match and '.' in match.group(1):
            item = origin(match.group(1))
            if item:
                addresses.append(item)
    if addresses:
        events.append({'kind': 'server_origin', 'origins': sorted(set(addresses))[:6], 'path_query_and_credentials_omitted': True})
    for kind, words in (('dns', ('dns', 'getaddrinfo', 'resolve', 'hostname')), ('tls', ('tls', 'ssl', 'x509', 'certificate', 'mbedtls')), ('tcp', ('connect()', 'connection refused', 'socket error', 'connect failed', 'connection timed out'))):
        if any(word in low for word in words):
            item = {'kind': kind, 'failure_marker': any(word in low for word in ('error', 'failed', 'failure', 'invalid', 'unable', 'refused', 'timeout', 'timed out', 'could not', "couldn't")), 'success_marker': any(word in low for word in ('success', 'established', 'verified'))}
            named = re.findall(r'\b(?:MBEDTLS_ERR_[A-Z0-9_]{1,80}|ESP_ERR_[A-Z0-9_]{1,80}|EAI_[A-Z0-9_]{1,40}|ECONNREFUSED|ETIMEDOUT|ENETUNREACH|EHOSTUNREACH|ECONNRESET)\b', line)
            numeric = re.findall(r'(?i)\b(?:reason|errno|err|error|code|ret|status)(?:[_ ]?code)?\s*[:=]\s*(-?(?:0x[0-9a-f]{1,8}|\d{1,8}))\b', line)
            numeric += re.findall(r'\((-0x[0-9a-fA-F]{1,8}|-\d{1,8})\)', line)
            if named:
                item['named_codes'] = sorted(set(named))[:8]
            if numeric:
                item['numeric_codes'] = numeric[:8]
            events.append(item)
    if 'http' in low or 'status_code' in low:
        codes = re.findall(r'(?i)(?:HTTP/[0-9.]+\s+|\b(?:http(?:[_ ](?:status|code))?|status(?:[_ ]code)?|response(?:[_ ]code)?)\s*[:= ]\s*)([1-5]\d\d)\b', line)
        events.append({'kind': 'http', 'status_codes': sorted(set(int(value) for value in codes)), 'failure_marker': any(word in low for word in ('failed', 'error', 'timeout', 'refused'))})
    version = re.search(r'(?i)\b(?:firmware(?:[_ ]version)?|app(?:[_ ]version)?|version|build)\s*[:= ]\s*["\x27]?(v?\d{1,4}(?:\.\d{1,4}){1,4}(?:[-+][A-Za-z0-9_.-]{1,48})?)\b', line)
    if version:
        events.append({'kind': 'version_candidate_unassigned_component', 'version': version.group(1)})
    idf = re.search(r'ESP-IDF\s+(v\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9_.-]{1,48})?)', line)
    if idf:
        events.append({'kind': 'runtime_build_version', 'version': idf.group(1)})
    if 'esp-rom:' in low or re.search(r'\brst:0x[0-9a-f]+', low):
        codes = re.findall(r'\b(?:POWERON_RESET|SW_CPU_RESET|SW_RESET|USB_UART_CHIP_RESET|RTC_SW_SYS_RST|BROWNOUT_RESET|TG0WDT_SYS_RESET|DEEPSLEEP_RESET)\b', line)
        events.append({'kind': 'boot_seen', 'reset_codes': sorted(set(codes))})
    if re.search(r'\bconnecting\b', low) and not any(item['kind'] == 'wifi' for item in events):
        events.append({'kind': 'connecting_marker', 'layer_unassigned': True})
    return events, 'typed_diagnostics' if events else 'unclassified_line_suppressed'

def main():
    counts = collections.Counter(); kinds = collections.Counter(); stored = []; seen = collections.Counter(); lines = 0; start = time.time()
    pending = ''
    def consume(line):
        nonlocal lines
        lines += 1
        events, category = sanitize(line)
        counts[category] += 1
        for item in events:
            kinds[item['kind']] += 1
            key = json.dumps(item, sort_keys=True)
            seen[key] += 1
            if len(stored) < 200 and seen[key] <= 5:
                stored.append({'observed_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), **item})
    while True:
        chunk = sys.stdin.read(1024)
        if not chunk:
            break
        pending += chunk
        while '\n' in pending or '\r' in pending:
            match = re.search(r'[\r\n]', pending)
            consume(pending[:match.start()])
            pending = pending[match.end():]
        if len(pending) > 8192:
            counts['oversized_fragment_suppressed'] += 1
            pending = ''
    if pending:
        consume(pending)
    result = {'ok': True, 'capture_started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(start)), 'capture_finished_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'lines_processed': lines, 'filter_counts': dict(counts), 'event_kind_counts': dict(kinds), 'events': stored, 'raw_lines_saved_or_printed': False, 'secret_chat_or_audio_output': False}
    with (ROOT / 'serial-connection-sanitized.json').open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps(result, sort_keys=True), flush=True)

if __name__ == '__main__':
    if sys.argv[1:] == ['--self-check']:
        assert sanitize('Authorization: Bearer SECRET_VALUE_123')[0] == []
        assert sanitize('wifi connected password=SECRET')[0] == []
        assert sanitize('server=https://pocket.example.invalid:10000/path?x=REDACTED_VALUE')[0][0]['origins'] == ['https://pocket.example.invalid:10000']
        assert sanitize('wifi disconnected reason=201')[0][0]['numeric_codes'] == ['201']
        assert sanitize('HTTP status_code=401')[0][0]['status_codes'] == [401]
        assert sanitize('App version: 0.1.72')[0][0]['version'] == '0.1.72'
        assert sanitize('ordinary private chat content')[0] == []
        assert 'numeric_codes' not in sanitize('I (20330) esp-tls: session initialized')[0][0]
        print(json.dumps({'ok': True, 'sanitizer_checks': 8, 'device_access': False}))
    else:
        main()
