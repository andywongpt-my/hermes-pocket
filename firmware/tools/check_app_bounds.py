"""Host-only app artifact check. Never opens a device or emits flash commands."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

APP_OFFSET = 0x10000
APP_LIMIT = 0x310000
APP_CAPACITY = APP_LIMIT - APP_OFFSET
ERASE_SECTOR = 0x1000


def check_size(size, offset=APP_OFFSET):
    if type(size) is not int or size <= 0 or size > APP_CAPACITY:
        raise ValueError('app size must be 1..0x300000 bytes')
    if offset != APP_OFFSET:
        raise ValueError('only app offset 0x10000 is permitted')
    erase_end = offset + ((size + ERASE_SECTOR - 1) // ERASE_SECTOR) * ERASE_SECTOR
    if erase_end > APP_LIMIT:
        raise ValueError('app-only sector erase crosses 0x310000')
    return erase_end


def check_partition(text):
    rows = [r for r in csv.reader(line for line in text.splitlines()
                                 if line.strip() and not line.lstrip().startswith('#'))]
    apps = [list(map(str.strip, r)) for r in rows if len(r) >= 2 and r[1].strip() in ('app', '0')]
    if len(apps) != 1:
        raise ValueError('exactly one factory app build slot required')
    r = apps[0]
    if len(r) < 5 or r[:3] != ['factory', 'app', 'factory']:
        raise ValueError('expected factory app slot')
    if int(r[3], 0) != APP_OFFSET or int(r[4], 0) != APP_CAPACITY:
        raise ValueError('factory slot must be [0x10000,0x310000)')
    # Other CSV rows are build placeholders, not device layout evidence.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True, type=Path)
    parser.add_argument('--partitions', required=True, type=Path)
    args = parser.parse_args()
    check_partition(args.partitions.read_text())
    data = args.app.read_bytes()
    end = check_size(len(data))
    print(json.dumps(dict(app_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                         app_offset=APP_OFFSET, app_capacity=APP_CAPACITY,
                         app_erase_end=end, app_limit=APP_LIMIT,
                         physical_partition_table_verified=False), sort_keys=True))


if __name__ == '__main__':
    main()
