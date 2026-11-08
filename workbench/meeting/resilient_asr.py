"""ASR failure never stops capture. Reopen recognition at a committed PCM offset."""
import asyncio
import audioop
import json
import time
from pathlib import Path
from .pipeline import atomic,append

async def run_channel(channel, segment_seconds=600, idle_seconds=2.5, retry_seconds=1):
    from . import volc_asr
    from .settings import settings
    run,source=channel.run,channel.source;path=run/(source+'.pcm')
    checkpoint=run/('asr-checkpoint-'+source+'.json');status=run/('asr-state-'+source+'.json')
    existing=[u for u in getattr(channel.memory,'utterances',[]) if u.get('source')==source]
    start=min(path.stat().st_size,max([int(u.get('end_ms',0))*48 for u in existing]+[0]))
    saved=json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    offset=min(path.stat().st_size,max(start,int(saved.get('byte_offset',0))))//2*2
    epoch=int(saved.get('epoch',0));failures=0;last_failed_size=None
    created=json.loads((run/'session.json').read_text()).get('created_at',time.time()) if (run/'session.json').exists() else time.time()
    def state(value,**extra):atomic(status,{'state':value,'byte_offset':offset,'audio_bytes':path.stat().st_size,'epoch':epoch,'updated_at':time.time(),**extra})
    def commit():atomic(checkpoint,{'byte_offset':offset,'epoch':epoch,'updated_at':time.time()})
    while True:
        size=path.stat().st_size;stopped=(run/'CAPTURE_STOPPED').exists()
        if size<=offset:
            if stopped:state('completed');return
            state('waiting_audio');await asyncio.sleep(.2);continue
        if last_failed_size is not None and size<=last_failed_size and not stopped:
            # No fake silence/keepalive audio; wait for actual additional PCM.
            await asyncio.sleep(.2);continue
        epoch+=1;begin=offset;read_end=offset;confirmed=offset;audio_started=None
        out=run/('volc-'+source)/('epoch-%06d'%epoch)
        state('connecting')
        async def chunks():
            nonlocal read_end,audio_started
            conversion=None;last_data=time.monotonic();paced=time.monotonic()
            with path.open('rb') as f:
                f.seek(begin)
                while read_end-begin<int(segment_seconds*48000):
                    data=f.read(min(9600,int(segment_seconds*48000)-(read_end-begin)))
                    if len(data)%2:f.seek(-1,1);data=data[:-1]
                    if not data:
                        if (run/'CAPTURE_STOPPED').exists() or time.monotonic()-last_data>=idle_seconds:return
                        await asyncio.sleep(.04);continue
                    # Modest catch-up rate; live input still arrives in real time.
                    await asyncio.sleep(max(0,paced-time.monotonic()));paced=max(paced,time.monotonic())+len(data)/48000/2
                    read_end+=len(data);last_data=time.monotonic();audio_started=audio_started or time.time()
                    converted,conversion=audioop.ratecv(data,2,1,24000,16000,conversion)
                    yield converted
        def connected():
            (run/('CONNECTED_'+source)).touch();(run/('ASR_READY_'+source)).write_text('provider_connected');state('recognizing')
        def segment(original):
            nonlocal confirmed,offset
            row=dict(original);base=begin//48
            row['start_ms']+=base;row['end_ms']+=base
            row['audio_start_wall']=created+row['start_ms']/1000;row['audio_end_wall']=created+row['end_ms']/1000
            row['speaker_epoch']=epoch;row['speaker_label']='识别段 %s · 声音 %s'%(epoch,row['speaker'].replace('speaker_',''))
            row['speaker_identification']='anonymous_connection_cluster_not_cross_connection_identity'
            channel.memory.add(row)
            confirmed=max(confirmed,min(read_end,row['end_ms']*48));offset=confirmed;commit()
        try:
            await volc_asr.stream_pcm(chunks(),settings(),channel.key,out,segment,connected,source)
            offset=read_end;commit();failures=0;last_failed_size=None
            state('waiting_audio' if not (run/'CAPTURE_STOPPED').exists() else 'draining')
        except asyncio.CancelledError:
            state('interrupted');raise
        except Exception as error:
            offset=confirmed;commit();failures+=1;last_failed_size=path.stat().st_size
            message=str(error) if str(error).startswith('VOLC_ASR_ERROR_') else type(error).__name__
            state('retry_wait',error=message,retries=failures)
            append(run/'asr-recovery.jsonl',{'time':time.time(),'source':source,'epoch':epoch,'byte_offset':offset,'read_end':read_end,'error':message})
            # Capture remains independent, even if recognition cannot currently run.
            if (run/'CAPTURE_STOPPED').exists() and failures>=3:
                state('pending_retry',error=message);raise RuntimeError('ASR pending; preserved audio needs retry') from None
            await asyncio.sleep(min(15,retry_seconds*2**min(failures-1,4)))
