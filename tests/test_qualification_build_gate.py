"""Qualification remains a separate explicit default-off application build."""
from pathlib import Path
import os,re,shutil,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class QualificationBuildGateTests(unittest.TestCase):
 def test_explicit_default_off_and_console_dependencies(self):
  text=(ROOT/'firmware/main/Kconfig.projbuild').read_text()
  block=re.search(r'^config POCKET_QUALIFICATION\n(.*?)(?=^config |^endmenu|\Z)',text,re.M|re.S)
  self.assertIsNotNone(block,'qualification option is missing')
  body=block.group(1)
  self.assertRegex(body,r'(?m)^    default n$')
  for dependency in ('POCKET_LIVE_PAIRED_COMPILE','!POCKET_OFFLINE_DIAGNOSTIC','ESP_CONSOLE_USB_SERIAL_JTAG','ESP_CONSOLE_SECONDARY_NONE'):
   self.assertIn(dependency,body)
  self.assertIn('CONFIG_POCKET_QUALIFICATION=n',(ROOT/'firmware/sdkconfig.defaults').read_text())
 def test_evaluated_source_and_incompatible_option_gate(self):
  cmake=os.environ.get('POCKET_CMAKE') or shutil.which('cmake')
  if not cmake:self.skipTest('CMake unavailable')
  stub='''set(COMPONENT_LIB main)
function(idf_component_register)
 file(APPEND "${RECEIPT}" "${ARGV}\\n")
endfunction()
function(target_sources)
 file(APPEND "${RECEIPT}" "${ARGV}\\n")
endfunction()
function(target_compile_definitions)
endfunction()
function(target_link_options)
endfunction()
function(idf_component_get_property out)
 set(${out} transport PARENT_SCOPE)
endfunction()
'''
  with tempfile.TemporaryDirectory() as td:
   p=Path(td);script=p/'gate.cmake';script.write_text(stub+'include("'+str(ROOT/'firmware/main/CMakeLists.txt')+'")\n')
   for enabled,offline in ((False,False),(True,False),(True,True)):
    with self.subTest(enabled=enabled,offline=offline):
     receipt=p/f'{enabled}-{offline}.txt'
     args=[cmake,'-DRECEIPT='+str(receipt),'-DCONFIG_POCKET_QUALIFICATION='+('ON' if enabled else 'OFF'),'-DCONFIG_POCKET_LIVE_PAIRED_COMPILE='+('ON' if enabled else 'OFF'),'-DCONFIG_POCKET_LIVE_WORKER_COMPILE='+('ON' if enabled else 'OFF'),'-DCONFIG_POCKET_OFFLINE_DIAGNOSTIC='+('ON' if offline else 'OFF'),'-P',str(script)]
     env=os.environ.copy();env.pop('POCKET_LINUX_PUBLIC_ORIGIN',None)
     r=subprocess.run(args,text=True,capture_output=True,env=env)
     if enabled and offline:
      self.assertNotEqual(r.returncode,0,'ambiguous diagnostic gestures must not be built');continue
     self.assertEqual(r.returncode,0,r.stdout+r.stderr)
     files=receipt.read_text().replace('\n',';').split(';')
     for name in ('../core/pocket_qualification.c','../core/pocket_qualification_protocol.c','../core/pocket_qualification_control.c','pocket_qualification_usb.c','pocket_qualification_transport.c','esp_driver_usb_serial_jtag'):
      self.assertEqual(files.count(name),int(enabled),name)
