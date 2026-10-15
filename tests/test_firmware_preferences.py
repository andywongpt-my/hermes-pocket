"""Actual preference C code with an in-memory ESP NVS boundary; no device access."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from test_firmware_http import HEADERS as HTTP_HEADERS

NVS = '''#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef unsigned nvs_handle_t;
#define NVS_READONLY 0
#define NVS_READWRITE 1
esp_err_t nvs_open(const char *,int,nvs_handle_t *);
esp_err_t nvs_get_u8(nvs_handle_t,const char *,uint8_t *);
esp_err_t nvs_set_u8(nvs_handle_t,const char *,uint8_t);
esp_err_t nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
'''
HARNESS = r'''
#include "pocket_preferences.h"
#include "nvs.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static int open_error,get_error,set_error,commit_error,opens,closes,writes,commits;
static uint8_t saved;
esp_err_t nvs_open(const char *space,int mode,nvs_handle_t *out){
 assert(!strcmp(space,"pocket_ui"));assert(mode==NVS_READONLY || mode==NVS_READWRITE);
 opens++;*out=7;return open_error;
}
esp_err_t nvs_get_u8(nvs_handle_t h,const char *key,uint8_t *out){assert(h==7 && !strcmp(key,"target"));*out=saved;return get_error;}
esp_err_t nvs_set_u8(nvs_handle_t h,const char *key,uint8_t value){assert(h==7 && !strcmp(key,"target"));writes++;if(!set_error)saved=value;return set_error;}
esp_err_t nvs_commit(nvs_handle_t h){assert(h==7);commits++;return commit_error;}
void nvs_close(nvs_handle_t h){assert(h==7);closes++;}
#define CHECK(c,m) do{if(!(c)){puts("FAIL: " m);return 1;}}while(0)
int main(void){
 saved=1;CHECK(pocket_preferences_load_target()==1,"saved Linux target not restored");
 CHECK(!writes && !commits,"boot must never write preferences");
 saved=2;CHECK(pocket_preferences_load_target()==0,"invalid target must fall back to Windows");
 get_error=-1;CHECK(pocket_preferences_load_target()==0,"missing target must fall back to Windows");get_error=0;
 open_error=-1;CHECK(pocket_preferences_load_target()==0,"failed NVS open must leave UI usable");open_error=0;
 int before=opens;CHECK(pocket_preferences_save_target(2)==ESP_ERR_INVALID_ARG && opens==before,"invalid target must not open or write NVS");
 saved=1;CHECK(pocket_preferences_save_target(1)==ESP_OK && !writes && !commits,"unchanged target caused flash wear");
 CHECK(pocket_preferences_save_target(0)==ESP_OK && saved==0 && writes==1 && commits==1,"deliberate target change not committed once");
 set_error=-2;CHECK(pocket_preferences_save_target(1)==-2 && commits==1,"failed set must not commit");set_error=0;
 commit_error=-3;CHECK(pocket_preferences_save_target(1)==-3,"commit failure was hidden");commit_error=0;
 get_error=-1;CHECK(pocket_preferences_save_target(0)==ESP_OK,"missing preference could not be created");get_error=0;
 CHECK(closes==opens-1,"successful NVS handles were not closed");
 puts("PASS: preference restoration, isolated key, write suppression and NVS failure handling");return 0;
}
'''


class FirmwarePreferencesTests(unittest.TestCase):
    def test_target_restoration_and_bounded_writes(self):
        compiler = shutil.which('clang') or shutil.which('gcc')
        cached = pathlib.Path(r'C:\Program Files\LLVM\bin\clang.exe')
        if not compiler and cached.is_file():
            compiler = str(cached)
        if not compiler:
            self.skipTest('host C compiler unavailable')
        source = pathlib.Path(__file__).resolve().parents[1]/'firmware/main'
        with tempfile.TemporaryDirectory() as directory:
            temp = pathlib.Path(directory)
            (temp/'esp_err.h').write_text(HTTP_HEADERS['esp_err.h'])
            (temp/'nvs.h').write_text(NVS)
            (temp/'fixture.c').write_text(HARNESS)
            binary = temp/('preferences.exe' if os.name == 'nt' else 'preferences')
            built = subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                                    '-I'+str(temp), '-I'+str(source), str(source/'pocket_preferences.c'),
                                    str(temp/'fixture.c'), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stdout+run.stderr)

