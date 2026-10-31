"""Evaluate CMake source inclusion for each compile gate; defaults stay inert."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'firmware' / 'main'

class LiveAppBuildGateTests(unittest.TestCase):
    def test_default_off_requires_paired_compile(self):
        kconfig = (MAIN / 'Kconfig.projbuild').read_text()
        match = re.search(r'^config POCKET_LIVE_APP_CALLER\n(.*?)(?=^config |^endmenu|\Z)', kconfig, re.M | re.S)
        self.assertIsNotNone(match, 'explicit caller option is missing')
        block = match.group(1)
        self.assertRegex(block, r'(?m)^    default n$')
        self.assertRegex(block, r'(?m)^    depends on POCKET_LIVE_PAIRED_COMPILE$')
        self.assertIn('CONFIG_POCKET_LIVE_APP_CALLER=n', (ROOT / 'firmware/sdkconfig.defaults').read_text())

    def test_evaluated_cmake_keeps_caller_sources_separate(self):
        cmake = os.environ.get('POCKET_CMAKE') or shutil.which('cmake')
        if not cmake:
            self.skipTest('CMake unavailable')
        stub = r'''
set(COMPONENT_LIB main)
function(idf_component_register)
  file(APPEND "${RECEIPT}" "${ARGV}\n")
endfunction()
function(target_sources)
  file(APPEND "${RECEIPT}" "${ARGV}\n")
endfunction()
function(target_link_options)
endfunction()
function(target_compile_definitions)
endfunction()
function(idf_component_get_property out)
  set(${out} transport PARENT_SCOPE)
endfunction()
'''
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            script = tmp / 'gate.cmake'
            script.write_text(stub + '\ninclude("' + str(MAIN / 'CMakeLists.txt') + '")\n')
            for paired, caller, diag in ((False,False,False),(True,False,False),(True,True,False),(True,True,True)):
                with self.subTest(paired=paired,caller=caller,diag=diag):
                    receipt = tmp / f'{paired}-{caller}-{diag}.txt'
                    flags = {'POCKET_LIVE_WORKER_COMPILE':paired,'POCKET_LIVE_PAIRED_COMPILE':paired,
                             'POCKET_LIVE_APP_CALLER':caller,'POCKET_OFFLINE_DIAGNOSTIC':diag}
                    command = [cmake, '-DRECEIPT='+str(receipt)] + ['-DCONFIG_'+key+'='+('ON' if value else 'OFF') for key,value in flags.items()] + ['-P',str(script)]
                    env = os.environ.copy(); env.pop('POCKET_LINUX_PUBLIC_ORIGIN',None)
                    result = subprocess.run(command, text=True, capture_output=True, env=env)
                    self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                    selected = receipt.read_text().replace('\n',';').split(';')
                    for name in ('pocket_live_app.c','pocket_live_admission.c'):
                        self.assertEqual(selected.count(name),int(caller),name)
                    self.assertEqual(selected.count('pocket_live_v2_worker.c'),int(paired))
                    self.assertEqual(selected.count('../core/pocket_offline_diagnostic.c'),int(diag))
