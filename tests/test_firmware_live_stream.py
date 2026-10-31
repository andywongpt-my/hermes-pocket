"""Actual bounded Live wire codec/state C; no device, network or provider calls."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


HARNESS = r'''
#include "pocket_live_stream.h"
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

static uint8_t pcm[960];
static uint8_t wire[977];
static pocket_live_packet packet(uint8_t kind) {
    pocket_live_packet p = {kind, 0, 0, 0, pcm, kind == 1 ? 960u : 2u};
    return p;
}
int main(int argc, char **argv) {
    assert(argc == 2);
    int which = atoi(argv[1]);
    pocket_live_packet p = packet(2), decoded;
    pocket_live_stream_state state, before;
    size_t written = 999;
    memset(pcm, 0x5a, sizeof pcm);
    memset(wire, 0xa5, sizeof wire);
    if (which == 1) {
        const uint8_t expected[] = {0x50,0x4c,1,2, 1,2,3,4, 5,6,7,8, 0,0,0,0, 0x34,0x12};
        pcm[0] = 0x34; pcm[1] = 0x12;
        p.sequence = 0x01020304; p.sample_offset = 0x05060708;
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) == POCKET_LIVE_OK);
        assert(written == sizeof expected && !memcmp(wire, expected, sizeof expected));
        assert(wire[written] == 0xa5);
        assert(pocket_live_unpack(expected, sizeof expected, &decoded) == POCKET_LIVE_OK);
        assert(decoded.kind == 2 && decoded.sequence == 0x01020304 && decoded.sample_offset == 0x05060708);
        assert(decoded.played_samples == 0 && decoded.pcm_bytes == 2 && decoded.pcm == expected + 16);
    } else if (which == 2) {
        p = packet(1); p.played_samples = 0x01020304;
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) == POCKET_LIVE_OK);
        assert(written == 976 && wire[3] == 1 && wire[12] == 1 && wire[13] == 2 && wire[14] == 3 && wire[15] == 4);
        assert(pocket_live_unpack(wire, written, &decoded) == POCKET_LIVE_OK);
        assert(decoded.pcm_bytes == 960 && decoded.played_samples == 0x01020304 && !memcmp(decoded.pcm, pcm, 960));
        assert(wire[976] == 0xa5);
    } else if (which == 3) {
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) == POCKET_LIVE_OK);
        for (size_t n = 0; n < 18; n++) assert(pocket_live_unpack(wire, n, &decoded) != POCKET_LIVE_OK);
        wire[0] = 'X'; assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK); wire[0] = 'P';
        wire[1] = 'X'; assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK); wire[1] = 'L';
        wire[2] = 2; assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK); wire[2] = 1;
        for (unsigned k = 0; k < 256; k++) if (k != 1 && k != 2) {
            wire[3] = (uint8_t)k; assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK);
        }
    } else if (which == 4) {
        for (size_t n = 0; n <= 962; n++) {
            p.pcm_bytes = n;
            bool valid = n >= 2 && n <= 960 && n % 2 == 0;
            assert((pocket_live_pack(&p, wire, sizeof wire, &written) == POCKET_LIVE_OK) == valid);
            if (valid) assert(pocket_live_unpack(wire, written, &decoded) == POCKET_LIVE_OK);
        }
        p = packet(1); p.pcm_bytes = 958;
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        wire[0] = 'P'; wire[1] = 'L'; wire[2] = 1; wire[3] = 1;
        assert(pocket_live_unpack(wire, 974, &decoded) != POCKET_LIVE_OK);
        wire[3] = 2;
        assert(pocket_live_unpack(wire, 977, &decoded) != POCKET_LIVE_OK);
    } else if (which == 5) {
        uint8_t old[sizeof wire]; memcpy(old, wire, sizeof wire);
        assert(pocket_live_pack(&p, wire, 17, &written) != POCKET_LIVE_OK && written == 0);
        assert(!memcmp(old, wire, sizeof wire));
        assert(pocket_live_pack(NULL, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        assert(pocket_live_pack(&p, NULL, sizeof wire, &written) != POCKET_LIVE_OK);
        assert(pocket_live_pack(&p, wire, sizeof wire, NULL) != POCKET_LIVE_OK);
        assert(pocket_live_unpack(NULL, 18, &decoded) != POCKET_LIVE_OK);
        assert(pocket_live_unpack(wire, 18, NULL) != POCKET_LIVE_OK);
        p.pcm = NULL; assert(pocket_live_pack(&p, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        assert(pocket_live_accept(NULL, &p, 0) != POCKET_LIVE_OK);
        pocket_live_state_init(&state, 1);
        assert(pocket_live_accept(&state, NULL, 0) != POCKET_LIVE_OK);
    } else if (which == 6) {
        p = packet(1); pocket_live_state_init(&state, 1);
        assert(pocket_live_accept(&state, &p, 0) == POCKET_LIVE_OK);
        assert(state.next_sequence == 1 && state.next_sample_offset == 480);
        before = state;
        assert(pocket_live_accept(&state, &p, 0) != POCKET_LIVE_OK && !memcmp(&state, &before, sizeof state));
        p.sequence = 2; p.sample_offset = 480;
        assert(pocket_live_accept(&state, &p, 0) != POCKET_LIVE_OK);
        p.sequence = 1; p.sample_offset = 479;
        assert(pocket_live_accept(&state, &p, 0) != POCKET_LIVE_OK);
        p.sample_offset = 480;
        assert(pocket_live_accept(&state, &p, 0) == POCKET_LIVE_OK);
        assert(state.next_sequence == 2 && state.next_sample_offset == 960);
    } else if (which == 7) {
        p = packet(1); pocket_live_state_init(&state, 1);
        p.played_samples = 2; before = state;
        assert(pocket_live_accept(&state, &p, 1) != POCKET_LIVE_OK && !memcmp(&state, &before, sizeof state));
        assert(pocket_live_accept(&state, &p, 2) == POCKET_LIVE_OK);
        p.sequence = 1; p.sample_offset = 480; p.played_samples = 1; before = state;
        assert(pocket_live_accept(&state, &p, 100) != POCKET_LIVE_OK && !memcmp(&state, &before, sizeof state));
        p.played_samples = 2; assert(pocket_live_accept(&state, &p, 100) == POCKET_LIVE_OK);
        p.sequence = 2; p.sample_offset = 960; p.played_samples = 100;
        assert(pocket_live_accept(&state, &p, 100) == POCKET_LIVE_OK && state.played_samples == 100);
    } else if (which == 8) {
        pocket_live_state_init(&state, 2);
        assert(pocket_live_accept(&state, &p, 0) == POCKET_LIVE_OK);
        p.sequence = 1; p.sample_offset = 1; p.pcm_bytes = 960;
        assert(pocket_live_accept(&state, &p, 0) == POCKET_LIVE_OK && state.next_sample_offset == 481);
        p.sequence = 2; p.sample_offset = 481; p.played_samples = 1; before = state;
        assert(pocket_live_accept(&state, &p, 100) != POCKET_LIVE_OK && !memcmp(&state, &before, sizeof state));
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        p.played_samples = 0; p.kind = 1;
        assert(pocket_live_accept(&state, &p, 100) != POCKET_LIVE_OK);
        pocket_live_state_init(&state, 99); p = packet(2);
        assert(pocket_live_accept(&state, &p, 0) != POCKET_LIVE_OK);
    } else if (which == 9) {
        pocket_live_state_init(&state, 2);
        state.next_sequence = p.sequence = UINT32_MAX;
        before = state;
        assert(pocket_live_accept(&state, &p, 0) != POCKET_LIVE_OK && !memcmp(&state, &before, sizeof state));
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        p.sequence = state.next_sequence = 0;
        p.sample_offset = state.next_sample_offset = UINT32_MAX;
        assert(pocket_live_accept(&state, &p, 0) != POCKET_LIVE_OK);
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        p.sample_offset = state.next_sample_offset = UINT32_MAX - 1;
        assert(pocket_live_accept(&state, &p, 0) == POCKET_LIVE_OK && state.next_sample_offset == UINT32_MAX);
        p.sample_offset = 0; p.pcm_bytes = SIZE_MAX;
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) != POCKET_LIVE_OK);
        assert(pocket_live_unpack(wire, SIZE_MAX, &decoded) != POCKET_LIVE_OK);
    } else if (which == 10) {
        assert(pocket_live_pack(&p, wire, sizeof wire, &written) == POCKET_LIVE_OK);
        wire[15] = 1;
        assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK);
        wire[15] = 0; memset(wire + 4, 0xff, 4);
        assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK);
        memset(wire + 4, 0, 4); memset(wire + 8, 0xff, 4);
        assert(pocket_live_unpack(wire, 18, &decoded) != POCKET_LIVE_OK);
    } else assert(0);
    return 0;
}
'''


class FirmwareLiveStreamTests(unittest.TestCase):
    def run_case(self, case):
        compiler = shutil.which('gcc')
        if not compiler:
            self.skipTest('GCC unavailable')
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            source = work / 'live.c'
            source.write_text(HARNESS)
            binary = work / 'live'
            result = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                '-Wconversion', '-Wshadow', '-pedantic', '-O2',
                '-I' + str(root / 'firmware/core'), str(source),
                str(root / 'firmware/core/pocket_live_stream.c'), '-o', str(binary)],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary), str(case)], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_literal_network_order_header_and_pcm_bytes(self): self.run_case(1)
    def test_microphone_exact_frame_and_playout_counter(self): self.run_case(2)
    def test_truncated_header_magic_version_and_kind(self): self.run_case(3)
    def test_payload_size_and_alignment_boundaries(self): self.run_case(4)
    def test_null_and_short_destination_fail_without_write(self): self.run_case(5)
    def test_microphone_replay_sequence_and_offsets(self): self.run_case(6)
    def test_rendered_counter_cannot_regress_or_run_ahead(self): self.run_case(7)
    def test_speaker_variable_frames_zero_ack_and_kind_isolation(self): self.run_case(8)
    def test_counter_and_size_overflow_fail_closed(self): self.run_case(9)
    def test_unpack_checks_semantics_not_just_header(self): self.run_case(10)
