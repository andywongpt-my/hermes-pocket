"""Offline numeric-only qualification measurements from synthetic paired PCM."""
from array import array
import json
import math
import os
from pathlib import Path
import struct
import unittest
from unittest.mock import patch
from pocket import qualification as q
from tests.test_qualification_host import NONCE, start
try:
    from pocket import qualification_analysis as a
except ImportError:
    a=None
LIBRARY=os.environ.get('POCKET_TEST_SPEEXDSP_LIBRARY')


class AnalysisContractTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(a,'qualification analysis module not implemented')

    def test_exact_independent_c_waveform_vectors_and_frame_generator(self):
        data=json.loads((Path(__file__).parent/'fixtures/qualification_waveform_v1.json').read_text())
        for index,value in data['vectors']:
            self.assertEqual(a.source_sample(index),value)
            self.assertEqual(a.source_frame(index,1),[value])
        for base in (0,95990,96240,143990,240000,479990,575999):
            self.assertEqual(a.source_frame(base,240),[a.source_sample(i) for i in range(base,base+240)])
        with self.assertRaises(q.QualificationError):a.source_frame(0,8193)

    def test_unknown_or_arbitrary_analysis_fields_never_serialize(self):
        for report in ({'raw':0},{'physical_acceptance':'true'},
                       {'physical_acceptance':True},{'physical_acceptance':float('inf')},
                       {'windows':[{'pcm':0}]}):
            with self.assertRaises(q.QualificationError):a.analysis_json(report)

    def test_closed_or_untrusted_capture_rejected(self):
        capture=q.Capture(1,NONCE);capture.close()
        with self.assertRaises(q.QualificationError):a.analyze_capture(capture,None)
        with self.assertRaises(q.QualificationError):a.analyze_capture(object(),None)


def synthetic_capture(*, silent=False, clipping=False):
    capture=q.Capture(2,NONCE)
    capture.accept(next(q.Parser().feed(start(2))))
    history=[0]*1024
    for frame in range(2400):
        base=frame*240
        ref=a.source_frame(base,240) if not silent else [0]*240
        pairs=[]
        for index,right in enumerate(ref):
            sample=base+index;history[sample%1024]=right
            near=int(.45*history[(sample-120)%1024]+.40*history[(sample-260)%1024]) if sample>=260 else 0
            near+=(sample%3)-1
            if clipping and frame==510 and index==0:near=32767
            pairs.extend((near,right))
        source=(frame+1)*240
        metadata=struct.pack('<8I',7,frame,(frame+1)*10,source,source,1,0,0)
        capture.accept(q.Record(17,2,NONCE,frame+1,base,metadata+struct.pack('<480h',*pairs)))
    terminal=struct.pack('<12I',7,0,1,2400,576000,144000,576000,24050,512,
                         80+2400*1024,1,0)
    capture.accept(q.Record(18,2,NONCE,2401,0,terminal))
    return capture


@unittest.skipUnless(LIBRARY,'explicit native Speex library required')
class NativeAnalysisTests(unittest.TestCase):
    def setUp(self):self.assertIsNotNone(a,'qualification analysis module not implemented')

    def test_real_paired_reference_measurements_and_no_physical_pass(self):
        capture=synthetic_capture();self.addCleanup(capture.close)
        report=a.analyze_capture(capture,LIBRARY,echo_only_confirmed=True)
        self.assertEqual(report['physical_acceptance'],0)
        self.assertEqual(report['usable_intervals'],2)
        self.assertEqual(report['rate_scheduling_uncertainty_known'],0)
        self.assertAlmostEqual(report['observed_rate_hz'],24000)
        # Compare every reported residual against an independent existing native
        # adapter. The quiet short windows need not reach the proposed10dB target.
        from pocket.live_echo import SpeexEchoCanceller
        powers=[[0,0,0] for _ in range(3)]
        with SpeexEchoCanceller(LIBRARY,frame_samples=240) as direct:
            for index in range(capture.frames):
                pair=struct.unpack_from('<480h',capture.raw,index*960)
                clean=struct.unpack('<240h',direct.process(struct.pack('<240h',*pair[::2]),
                    struct.pack('<240h',*pair[1::2])))
                for at,(begin,end) in enumerate(((500,599),(1100,1199),(1900,1999))):
                    if begin<=index<end:
                        powers[at][0]+=sum(x*x for x in pair[::2])
                        powers[at][1]+=sum(x*x for x in clean);powers[at][2]+=240
        expected=[10*math.log10(max(x/n,1)/max(y/n,1)) for x,y,n in powers]
        for window,erle in zip(report['windows'],expected):
            self.assertEqual(window['adaptation_excluded_frames'],100)
            self.assertAlmostEqual(window['erle_db'],erle,places=10)
            self.assertGreater(window['reference_source_correlation'],.95)
            self.assertGreater(window['microphone_snr_db'],10)
            self.assertEqual(window['echo_metric_usable'],int(window['index'] in (0,2)))
        self.assertEqual(report['erle_target_met_intervals'],sum(value>=10 for index,value in enumerate(expected) if index in (0,2)))
        self.assertNotIn('pcm',a.analysis_json(report))

    def test_middle_window_is_not_an_echo_acceptance_interval(self):
        capture=synthetic_capture();self.addCleanup(capture.close)
        report=a.analyze_capture(capture,LIBRARY,echo_only_confirmed=True)
        self.assertEqual(report['windows'][1]['echo_only_confirmed'],0)
        self.assertEqual(report['windows'][1]['echo_metric_usable'],0)
        self.assertEqual(report['windows'][1]['near_end_assessment_required'],1)

    def test_source_reconstruction_never_replaces_actual_reference_for_aec(self):
        capture=synthetic_capture();self.addCleanup(capture.close)
        real=a.analyze_capture(capture,LIBRARY,echo_only_confirmed=True)
        with patch.object(a,'source_frame',lambda start,count:[0]*count):
            changed=a.analyze_capture(capture,LIBRARY,echo_only_confirmed=True)
        self.assertEqual([w['erle_db'] for w in real['windows']],
                         [w['erle_db'] for w in changed['windows']])
        self.assertEqual(changed['usable_intervals'],0)

    def test_weak_reference_unconfirmed_echo_and_clipping_are_inconclusive(self):
        for arguments in ({'silent':True},{'clipping':True},{}):
            with self.subTest(arguments=arguments):
                capture=synthetic_capture(**arguments)
                try:
                    report=a.analyze_capture(capture,LIBRARY,echo_only_confirmed=bool(arguments))
                    self.assertEqual(report['physical_acceptance'],0)
                    self.assertLess(report['usable_intervals'],2)
                    if arguments.get('silent'):self.assertEqual(report['usable_intervals'],0)
                    if not arguments:self.assertEqual(report['usable_intervals'],0)
                finally:capture.close()

class ReviewRateTests(unittest.TestCase):
    def test_unbounded_short_quantization_interval_is_unavailable(self):
        from tests.test_qualification_host import pcm
        capture=q.Capture(1,NONCE);capture.accept(next(q.Parser().feed(start())))
        for index,elapsed in enumerate((10,11)):
            capture.accept(next(q.Parser().feed(pcm(index,elapsed_ms=elapsed))))
        report=a.analyze_capture(capture,None)
        self.assertEqual(report['observed_rate_available'],0)
        self.assertEqual(report['observed_rate_hz'],0)
