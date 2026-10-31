"""Actual C core/export fixture -> actual host collector parser, synthetic only."""
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from pocket.qualification import Capture, Parser


class CoreHostTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(__file__).resolve().parents[1]
        if not shutil.which('gcc'):raise unittest.SkipTest('GCC unavailable')
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.binary=Path(cls.temp.name)/'export'
        args=['gcc','-std=c11','-Wall','-Wextra','-Werror','-Wconversion','-Wshadow','-pedantic',
              '-I'+str(cls.root/'firmware/core'),str(cls.root/'tests/fixtures/qualification_core_export.c'),
              str(cls.root/'firmware/core/pocket_qualification.c'),
              str(cls.root/'firmware/core/pocket_qualification_protocol.c'),'-o',str(cls.binary)]
        result=subprocess.run(args,capture_output=True,text=True,timeout=20)
        if result.returncode:raise AssertionError(result.stdout+result.stderr)

    async def exercise(self,mode,profile,frames,complete,result_code,cleanup):
        process=await asyncio.create_subprocess_exec(str(self.binary),mode,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,
            env={'PATH':os.environ.get('PATH',''),'LANG':'C'})
        parser=Parser();capture=Capture(profile,0x1020304050607080)
        try:
            async with asyncio.timeout(10):
                while data:=await process.stdout.read(4096):
                    for item in parser.feed(data):capture.accept(item)
                await process.wait()
            self.assertEqual(process.returncode,0,(await process.stderr.read()).decode())
            self.assertEqual(await process.stderr.read(),b'')
            self.assertTrue(capture.terminal_received)
            self.assertEqual(capture.complete,complete)
            if frames is not None:self.assertEqual(capture.frames,frames)
            self.assertEqual(capture.report()['result'],result_code)
            self.assertEqual(capture.report()['cleanup'],cleanup)
            self.assertLessEqual(len(capture.raw),2304000)
            self.assertLessEqual(parser.peak_scratch_bytes,8192)
            self.assertLessEqual(parser.wire_bytes,3145728)
        finally:
            capture.close()
            if process.returncode is None:process.kill();await process.wait()

    async def test_silent_exact_terminal(self):await self.exercise('silent',1,1000,True,0,1)
    async def test_audio_exact_terminal(self):await self.exercise('audio',2,2400,True,0,1)
    async def test_stop_not_complete(self):await self.exercise('stop',2,None,False,1,1)
    async def test_full_frame_count_with_bad_cleanup_not_complete(self):
        await self.exercise('cleanup_error',1,1000,False,10,2)
