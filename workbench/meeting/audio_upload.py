"""Resumable PCM append: offsets + byte comparison + durable acknowledgement."""
import os
import struct
import time

class OffsetConflict(ValueError): pass

def append_frame(path, frame):
    if len(frame) < 10 or len(frame) > 48008 or (len(frame)-8) % 2:
        raise ValueError('invalid PCM frame')
    offset = struct.unpack('>Q',frame[:8])[0]; data = frame[8:]
    if offset % 2: raise ValueError('unaligned PCM offset')
    # The service enforces a single writer for this source.
    with path.open('r+b',buffering=0) as f:
        size = f.seek(0,2)
        if offset > size: raise OffsetConflict('missing preceding audio')
        overlap = min(len(data),size-offset)
        if overlap:
            f.seek(offset)
            if f.read(overlap) != data[:overlap]: raise OffsetConflict('audio at offset differs')
        if overlap < len(data):
            f.seek(0,2); remaining=memoryview(data)[overlap:]
            while remaining:
                count=f.write(remaining)
                if not count: raise OSError('audio write stalled')
                remaining=remaining[count:]
        os.fsync(f.fileno())
        return max(size,offset+len(data))

def capture_state(session):
    sources={};now=time.time()
    for source in session.meta['sources']:
        p=session.run/(source+'.pcm');size=p.stat().st_size if p.exists() else 0
        last=session.last_audio.get(source,p.stat().st_mtime if size else None)
        age=max(0,now-last) if last is not None else None
        connected=source in session.connections
        state=('receiving' if connected and age is not None and age<5 else
               'stalled' if connected else 'disconnected')
        if session.meta['status'] not in ('recording','finishing'):state='stopped'
        sources[source]={'state':state,'connected':connected,'durable_bytes':size,'last_audio_wall':last,'idle_seconds':round(age,1) if age is not None else None}
    return {'manual_control':True,'sources':sources,'state':sources.get('microphone',{}).get('state','stopped')}
