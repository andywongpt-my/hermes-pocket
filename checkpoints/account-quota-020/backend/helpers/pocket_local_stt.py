#!/usr/bin/env python3
"""MIT. Offline, bounded Pocket STT in an existing faster-whisper environment.

This helper never downloads models, reads credentials or changes Hermes settings.
Run with the existing Python interpreter that already has faster-whisper installed.
"""
import argparse
import os
from pathlib import Path
import re
import sys
import wave

MAX_FRAMES=480000
MAX_TEXT_BYTES=4096

def model_path(value):
    path=Path(value).expanduser()
    if not path.is_absolute() or not path.is_dir() or not (path/'model.bin').is_file():
        raise ValueError('an existing absolute cached model directory is required')
    return path.resolve()

def load_model(path,factory=None):
    path=model_path(path)
    # Local-only mode is enforced even if a caller inherited online HF settings.
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    if factory is None:
        from faster_whisper import WhisperModel
        factory=WhisperModel
    return factory(str(path),device='cpu',compute_type='int8',cpu_threads=2,
                   num_workers=1,local_files_only=True)

def transcribe(audio,model,language='auto',*,factory=None):
    model=model_path(model)
    audio=Path(audio)
    if not audio.is_file() or audio.stat().st_size>1000000:
        raise ValueError('bounded PCM WAV input required')
    with wave.open(str(audio),'rb') as stream:
        if (stream.getnchannels()!=1 or stream.getsampwidth()!=2 or
            stream.getframerate()!=16000 or stream.getcomptype()!='NONE' or
            not 0<stream.getnframes()<=MAX_FRAMES):
            raise ValueError('input must be 16 kHz mono PCM, at most 30 seconds')
        frames=stream.getnframes()
        if len(stream.readframes(frames))!=frames*2:
            raise ValueError('truncated WAV input')
    if language not in ('','auto') and not re.fullmatch('[a-z]{2,3}',language):
        raise ValueError('invalid language')
    engine=load_model(model,factory)
    segments,_=engine.transcribe(str(audio),language=None if language in ('','auto') else language,
        beam_size=1,vad_filter=True,condition_on_previous_text=False,
        no_speech_threshold=.6,log_prob_threshold=-1)
    parts=[];length=0
    for segment in segments:
        if getattr(segment,'no_speech_prob',0)>.6 and getattr(segment,'avg_logprob',0)<-1:
            continue
        text=segment.text.strip()
        if not text:continue
        length+=len(text.encode('utf-8'))+(1 if parts else 0)
        if length>MAX_TEXT_BYTES:raise ValueError('transcript exceeds Pocket limit')
        parts.append(text)
    result=' '.join(parts).strip()
    if not result:raise ValueError('no confident speech detected')
    return result

def write_transcript(path,text):
    # The Hermes command runner supplies a new path in its temporary directory.
    # Exclusive creation prevents accidental overwrite or following a symlink.
    fd=os.open(os.fspath(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as output:output.write(text+'\n')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir',required=True)
    parser.add_argument('--input');parser.add_argument('--output')
    parser.add_argument('--language',default='auto')
    parser.add_argument('--check',action='store_true',help='load cached model without transcribing audio')
    args=parser.parse_args()
    try:
        if args.check:
            load_model(args.model_dir)
            print('Pocket local STT runtime and cached model loaded; no audio processed.')
            return
        if not args.input or not args.output:raise ValueError('input and output are required')
        result=transcribe(args.input,args.model_dir,args.language)
        write_transcript(args.output,result)
    except Exception:
        # Do not leak input paths, audio, transcripts or inherited environment.
        print('Pocket local STT failed. Check local runtime, cached model and PCM input.',file=sys.stderr)
        raise SystemExit(2) from None
if __name__=='__main__':main()
