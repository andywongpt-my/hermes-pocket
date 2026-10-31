"""Bounded offline measurements, never physical acceptance or audio output.

Only actual captured RIGHT samples enter native Speex. Reconstructed waveform1
is a correlation aid. Lane support, ERLE and observed rate are conditional
measurements, not an SPL, intelligibility, clock-calibration or hardware pass.
No device/network/provider API or raw-file writer is imported or used.
"""
from contextlib import contextmanager
import ctypes
import json
import math
import os
import struct
from .qualification import Capture, QualificationError, _finite_number, _integer

WINDOWS = ((96000, 144000), (240000, 288000), (432000, 480000))
ROOT_FIELDS = frozenset(('version','profile','frames','protocol_complete','physical_acceptance',
    'source_only_for_correlation','observed_rate_hz','observed_rate_available',
    'rate_quantization_halfwidth_hz','rate_scheduling_uncertainty_known',
    'timestamp_observation_kind','clipped_samples','usable_intervals','erle_target_met_intervals'))
WINDOW_FIELDS = frozenset(('index','start_frame','end_frame','analysis_start_frame','analysis_end_frame',
    'adaptation_excluded_frames','measurement_frames','baseline_frames','noise_floor_rms',
    'reference_rms','microphone_rms','residual_rms','baseline_microphone_rms','baseline_reference_rms',
    'reference_snr_db','microphone_snr_db','minimum_usable_snr_db','erle_db','erle_target_db',
    'clipped_samples','echo_only_confirmed','near_end_assessment_required','lane_hypothesis_supported','echo_metric_usable',
    'reference_source_correlation','reference_source_gain','reference_source_lag_samples',
    'microphone_source_correlation','microphone_source_gain','microphone_source_lag_samples'))


def _white(index):
    value = (index + 0x9e3779b9) & 0xffffffff
    value ^= value >> 16; value = (value * 0x7feb352d) & 0xffffffff
    value ^= value >> 15; value = (value * 0x846ca68b) & 0xffffffff
    value ^= value >> 16
    return (value & 65535)-32768


def _trunc(value, denominator):
    return (abs(value)//denominator) * (-1 if value < 0 else 1)


def source_sample(offset):
    if not _integer(offset): raise QualificationError('invalid_source_offset')
    for start,end in WINDOWS:
        if start <= offset < end:
            acc = sum(_white((offset-i)&0xffffffff) for i in range(8))
            acc -= sum(_white((offset-i)&0xffffffff) for i in range(8,16))
            gain = min(offset-start,end-1-offset,240)
            return _trunc(_trunc(acc,1024)*gain,240)
    return 0


def source_frame(offset, count):
    """O(count) bounded reconstruction; NOT the captured AEC reference."""
    if not _integer(offset) or not _integer(count,8192) or offset+count > (1 << 32):
        raise QualificationError('invalid_source_span')
    if not count: return []
    white = [_white((offset-15+i)&0xffffffff) for i in range(count+15)]
    older,newer = sum(white[:8]),sum(white[8:16])
    result = []
    for i in range(count):
        sample=offset+i; value=0
        for start,end in WINDOWS:
            if start <= sample < end:
                value=_trunc(_trunc(newer-older,1024)*min(sample-start,end-1-sample,240),240)
                break
        result.append(value)
        if i+1 < count:
            older += white[i+8]-white[i]
            newer += white[i+16]-white[i+8]
    return result


@contextmanager
def _native_echo(path):
    """Explicit trusted SpeexDSP1.2.1 public ABI; no library discovery/download."""
    if not isinstance(path,(str,os.PathLike)) or not os.path.isabs(path):
        raise QualificationError('explicit_native_echo_required')
    try:
        lib=ctypes.CDLL(os.fspath(path))
        lib.speex_echo_state_init.argtypes=[ctypes.c_int,ctypes.c_int]
        lib.speex_echo_state_init.restype=ctypes.c_void_p
        lib.speex_echo_state_destroy.argtypes=[ctypes.c_void_p]
        lib.speex_echo_state_destroy.restype=None
        lib.speex_echo_ctl.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p]
        lib.speex_echo_ctl.restype=ctypes.c_int
        lib.speex_echo_cancellation.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(ctypes.c_int16)]*3
        lib.speex_echo_cancellation.restype=None
    except (OSError,AttributeError,TypeError,ValueError):
        raise QualificationError('native_echo_unavailable') from None
    state=lib.speex_echo_state_init(240,4800)
    if not state: raise QualificationError('native_echo_allocation_failed')
    samples=ctypes.c_int16*240
    try:
        rate,actual,frame=ctypes.c_int(24000),ctypes.c_int(),ctypes.c_int()
        if (lib.speex_echo_ctl(state,24,ctypes.byref(rate)) or
                lib.speex_echo_ctl(state,25,ctypes.byref(actual)) or
                lib.speex_echo_ctl(state,3,ctypes.byref(frame)) or
                actual.value!=24000 or frame.value!=240):
            raise QualificationError('native_echo_format_mismatch')
        def process(microphone,reference):
            near,far,output=samples(*microphone),samples(*reference),samples()
            lib.speex_echo_cancellation(state,near,far,output)
            return output
        yield process
    finally:
        lib.speex_echo_state_destroy(state)


def _lane_match(raw, sample_start, lane):
    # Search a documented +/-100ms range on a bounded2048-sample segment.
    # Coarse8-sample search then full-sample refinement. This can be ambiguous;
    # it does not identify physical lanes or establish unrestricted delay bounds.
    count,limit=2048,2400
    if sample_start<limit or sample_start+count>len(raw)//4:return (0.,0.,0)
    observed=[struct.unpack_from('<h',raw,4*(sample_start+i)+2*lane)[0] for i in range(count)]
    source=source_frame(sample_start-limit,count+2*limit)
    def metric(lag,stride):
        indexes=range(0,count,stride);n=len(indexes)
        xmean=sum(observed[i] for i in indexes)/n
        ymean=sum(source[i+limit-lag] for i in indexes)/n
        xx=yy=xy=0.
        for i in indexes:
            x=observed[i]-xmean;y=source[i+limit-lag]-ymean
            xx+=x*x;yy+=y*y;xy+=x*y
        return (xy/math.sqrt(xx*yy),xy/yy) if xx>0 and yy>0 else (0.,0.)
    best=(0.,0.,0)
    for lag in range(-limit,limit+1,8):
        corr,gain=metric(lag,8)
        if abs(corr)>abs(best[0]):best=(corr,gain,lag)
    center=best[2];best=(0.,0.,0)
    for lag in range(max(-limit,center-7),min(limit,center+7)+1):
        corr,gain=metric(lag,1)
        if abs(corr)>abs(best[0]):best=(corr,gain,lag)
    return best


def _base_report(capture):
    delta=capture.elapsed_ms[-1]-capture.elapsed_ms[0] if capture.frames>=2 else 0
    samples=(capture.frames-1)*240
    rate=samples*1000/delta if delta>2 else 0.
    # +/-1ms endpoint quantization is only a lower uncertainty component.
    halfwidth=(samples*1000/(delta-2)-samples*1000/(delta+2))/2 if delta>2 else 0.
    return dict(version=1,profile=capture.profile,frames=capture.frames,
        protocol_complete=int(capture.complete),physical_acceptance=0,source_only_for_correlation=1,
        observed_rate_hz=rate,observed_rate_available=int(delta>2),
        rate_quantization_halfwidth_hz=halfwidth,rate_scheduling_uncertainty_known=0,
        timestamp_observation_kind=1,clipped_samples=0,usable_intervals=0,
        erle_target_met_intervals=0,windows=[])


def analyze_capture(capture, library_path, *, echo_only_confirmed=False, adaptation_frames=100):
    """Measure explicit echo-only windows, excluding adaptation and end ramp.

    Missing physical/lane evidence is never supplied by this function. Defaults
    do not assert echo-only conditions. No retained cleaned PCM or input copy is
    created. Native filter state and bounded per-frame/segment scratch are extra
    RAM, not part of the capture's <=2,304,000-byte retained raw-PCM buffer.
    """
    if (not isinstance(capture,Capture) or capture.closed or capture.start_fields is None or
            type(echo_only_confirmed) is not bool or not _integer(adaptation_frames,150,50)):
        raise QualificationError('invalid_analysis_input')
    report=_base_report(capture)
    stats=[]
    for index,(start,end) in enumerate(WINDOWS):
        stats.append(dict(index=index,start=start//240,end=end//240,begin=start//240+adaptation_frames,
            finish=end//240-1,n=0,base_n=0,mic=0,ref=0,out=0,base_mic=0,base_ref=0,clips=0))
    def measure(process=None):
        for index in range(capture.frames):
            pairs=struct.unpack_from('<480h',capture.raw,index*960)
            mic,ref=pairs[::2],pairs[1::2]
            output=process(mic,ref) if process else ()
            clips=sum(value in (-32768,32767) for value in pairs)
            report['clipped_samples']+=clips
            for item in stats:
                if item['start']-100 <= index < item['start']:
                    item['base_n']+=240
                    item['base_mic']+=sum(x*x for x in mic)
                    item['base_ref']+=sum(x*x for x in ref)
                if item['begin'] <= index < item['finish']:
                    item['n']+=240;item['mic']+=sum(x*x for x in mic)
                    item['ref']+=sum(x*x for x in ref);item['out']+=sum(x*x for x in output)
                    item['clips']+=clips
    if capture.profile==1:
        measure();return report
    with _native_echo(library_path) as process:measure(process)
    for item in stats:
        n,baseline=max(item['n'],1),max(item['base_n'],1)
        mic,ref,out=item['mic']/n,item['ref']/n,item['out']/n
        base_mic,base_ref=item['base_mic']/baseline,item['base_ref']/baseline
        ref_snr=10*math.log10(max(ref,1)/max(base_ref,1))
        mic_snr=10*math.log10(max(mic,1)/max(base_mic,1))
        erle=10*math.log10(max(mic,1)/max(out,1))
        right=_lane_match(memoryview(capture.raw)[:capture.raw_bytes],item['begin']*240,1)
        left=_lane_match(memoryview(capture.raw)[:capture.raw_bytes],item['begin']*240,0)
        supported=abs(right[0])>=.6 and abs(right[0])-abs(left[0])>=.1
        declared_echo_only=echo_only_confirmed and item['index'] in (0,2)
        usable=(capture.complete and declared_echo_only and item['n']>=25*240 and
                item['base_n']>=100*240 and ref_snr>=10 and mic_snr>=10 and
                not item['clips'] and supported)
        window=dict(index=item['index'],start_frame=item['start'],end_frame=item['end'],
            analysis_start_frame=item['begin'],analysis_end_frame=item['finish'],
            adaptation_excluded_frames=adaptation_frames,measurement_frames=item['n']//240,
            baseline_frames=item['base_n']//240,noise_floor_rms=1,
            reference_rms=math.sqrt(ref),microphone_rms=math.sqrt(mic),residual_rms=math.sqrt(out),
            baseline_microphone_rms=math.sqrt(base_mic),baseline_reference_rms=math.sqrt(base_ref),
            reference_snr_db=ref_snr,microphone_snr_db=mic_snr,minimum_usable_snr_db=10,
            erle_db=erle,erle_target_db=10,clipped_samples=item['clips'],
            echo_only_confirmed=int(declared_echo_only),near_end_assessment_required=int(item['index']==1),
            lane_hypothesis_supported=int(supported),
            echo_metric_usable=int(usable),reference_source_correlation=right[0],
            reference_source_gain=right[1],reference_source_lag_samples=right[2],
            microphone_source_correlation=left[0],microphone_source_gain=left[1],
            microphone_source_lag_samples=left[2])
        report['windows'].append(window)
        report['usable_intervals']+=int(usable)
        report['erle_target_met_intervals']+=int(usable and erle>=10)
    return report


def analysis_json(report):
    if not isinstance(report,dict) or set(report)-ROOT_FIELDS-{'windows'}:
        raise QualificationError('invalid_analysis_report')
    for key,value in report.items():
        if key=='windows':
            if not isinstance(value,list) or len(value)>3:
                raise QualificationError('invalid_analysis_report')
            for window in value:
                if not isinstance(window,dict) or set(window)-WINDOW_FIELDS or any(not _finite_number(v) for v in window.values()):
                    raise QualificationError('invalid_analysis_report')
        elif not _finite_number(value):raise QualificationError('invalid_analysis_report')
    if report.get('physical_acceptance',0)!=0 or report.get('rate_scheduling_uncertainty_known',0)!=0:
        raise QualificationError('invalid_analysis_claim')
    text=json.dumps(report,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(text)>16384:raise QualificationError('analysis_report_budget')
    return text
