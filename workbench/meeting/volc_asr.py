"""Doubao ASR 2.0: continuous PCM16, two-pass final segments, anonymous speakers.

Wire contract: official Volcengine large-model-streaming ASR documentation.
No TTS, recording startup, voiceprint enrollment, model fallback, or TMCRA writes.
"""
import asyncio
import audioop
import gzip
import json
import struct
import time
import uuid
import wave
from pathlib import Path
import websockets

ENDPOINTS = {
    "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_async",
    "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async",
}

def packet(kind, payload, sequence, final=False, is_json=False):
    if is_json:
        payload = json.dumps(payload, ensure_ascii=False).encode()
    payload = gzip.compress(payload)
    flags = 3 if final else 1
    header = bytes((0x11, (kind << 4) | flags, (0x10 if is_json else 0) | 1, 0))
    return header + struct.pack(">iI", -abs(sequence) if final else sequence, len(payload)) + payload

def decode(raw):
    if not isinstance(raw, bytes) or len(raw) < 8 or raw[0] >> 4 != 1:
        raise ValueError("invalid ASR frame")
    offset = (raw[0] & 15) * 4
    kind, flags, codec, compression = raw[1] >> 4, raw[1] & 15, raw[2] >> 4, raw[2] & 15
    sequence = 0
    error = None
    if kind == 15:
        error = struct.unpack_from(">I", raw, offset)[0]
        offset += 4
    elif kind == 9:
        if flags & 1:
            sequence = struct.unpack_from(">i", raw, offset)[0]
            offset += 4
    else:
        raise ValueError("unexpected ASR message type")
    size = struct.unpack_from(">I", raw, offset)[0]
    offset += 4
    if size > 4 * 1024 * 1024 or len(raw) != offset + size:
        raise ValueError("invalid ASR payload size")
    payload = raw[offset:]
    if compression == 1:
        payload = gzip.decompress(payload)
    elif compression != 0:
        raise ValueError("unknown ASR compression")
    if error is not None:
        # Do not echo a vendor body which might contain request credentials.
        raise RuntimeError("VOLC_ASR_ERROR_" + str(error))
    if codec != 1:
        raise ValueError("expected JSON ASR response")
    body = json.loads(payload)
    code = body.get("code")
    if code not in (None, 0, 1000, 20000000):
        raise RuntimeError("VOLC_ASR_ERROR_" + str(code))
    return body, bool(flags & 2 or sequence < 0)

def request_body(diarization=True):
    return {
        "user": {"uid": "tmcra-isolated-recording-test"},
        "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        "request": {
            "model_name": "bigmodel", "enable_nonstream": True,
            "enable_speaker_info": diarization, "ssd_version": "200",
            "show_utterances": True, "enable_itn": True, "enable_punc": True,
            "enable_ddc": False, "result_type": "full", "end_window_size": 800,
        },
    }

class Segments:
    def __init__(self, session_id, source="microphone", start_wall=None):
        self.session_id, self.source = session_id, source
        self.start_wall = time.time() if start_wall is None else start_wall
        self.rows = {}
        self.first_partial_seconds = None
        self.first_final_seconds = None
        self.final_latencies = []

    def accept(self, body, elapsed):
        result = body.get("result", {})
        if isinstance(result, list):
            result = result[0] if result else {}
        if result.get("text") and self.first_partial_seconds is None:
            self.first_partial_seconds = elapsed
        changes = []
        for u in result.get("utterances", []):
            if u.get("definite") is not True or not str(u.get("text", "")).strip():
                continue
            words=u.get('words',[])
            start=u.get('start_time')
            end=u.get('end_time')
            timestamp_source='utterance'
            if start is None:
                # English space tokens carry -1 placeholders, not audio bounds.
                starts=[w['start_time'] for w in words if type(w.get('start_time')) in (int,float) and w['start_time']>=0]
                if not starts:continue
                start=min(starts);timestamp_source='word_start_fallback'
            if end is None:
                ends=[w['end_time'] for w in words if type(w.get('end_time')) in (int,float) and w['end_time']>=0]
                if not ends:continue
                end=max(ends);timestamp_source='word_bounds_fallback'
            start,end=int(start),int(end)
            if start < 0 or end < start:
                raise ValueError("invalid ASR timestamp")
            sid = u.get("speaker_id", u.get("additions", {}).get("speaker_id"))
            speaker = "unknown" if sid is None or str(sid) in ("", "-1") else "speaker_" + str(sid)
            identity = f"volc:{self.session_id}:{start}-{end}"
            row = {"id": identity, "source": self.source, "speaker": speaker,
                   "speaker_identification": "anonymous_session_cluster_not_person_identity",
                   "text": u["text"], "start_ms": start, "end_ms": end,
                   "audio_start_wall": self.start_wall + start / 1000,
                   "audio_end_wall": self.start_wall + end / 1000,
                   "completed_wall": self.start_wall + elapsed, "definite": True,
                   "asr_provider": "doubao-seed-asr-2.0","timestamp_source":timestamp_source}
            previous = self.rows.get(identity)
            if previous and all(previous[k] == row[k] for k in ("text", "speaker")):
                continue
            # A later final pass may split or replace an earlier interval. Replace it,
            # rather than count revised text twice. Raw events remain the audit trail.
            supersedes = [k for k, old in self.rows.items() if k != identity
                          and old["start_ms"] < end and start < old["end_ms"]]
            for k in supersedes:
                del self.rows[k]
            row["supersedes_utterance_ids"] = supersedes
            self.rows[identity] = row
            changes.append(row)
            if self.first_final_seconds is None:
                self.first_final_seconds = elapsed
            if previous is None:
                self.final_latencies.append(round(elapsed - end / 1000, 3))
        return changes

    def all(self):
        return sorted(self.rows.values(), key=lambda x: (x["start_ms"], x["end_ms"]))

async def stream_pcm(chunks, config, key, output_dir, on_segment=None, on_connected=None, source="microphone"):
    endpoint = config["asr_endpoint"]
    if endpoint not in ENDPOINTS:
        raise ValueError("unexpected ASR host")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    segments = Segments(uuid.uuid4().hex, source)
    sent = 0
    finished = False
    req_id = str(uuid.uuid4())
    headers = {"X-Api-Key": key, "X-Api-Resource-Id": config["asr_resource"],
               "X-Api-Request-Id": req_id, "X-Api-Connect-Id": req_id}
    async with websockets.connect(endpoint, additional_headers=headers, open_timeout=15,
                                  max_size=4 * 1024 * 1024, close_timeout=3, proxy=None) as ws:
        await ws.send(packet(1, request_body(config.get("diarization", True)), 1, is_json=True))
        # Handshake 101 alone is not authorization. The first ASR response must
        # succeed before marking recognition ready; capture storage runs independently.
        initial, initial_final = decode(await asyncio.wait_for(ws.recv(), 15))
        if initial_final:
            raise RuntimeError("ASR ended during initialization")
        with (output_dir / "volc-events.jsonl").open("a") as f:
            f.write(json.dumps({"elapsed":time.monotonic()-started,"phase":"initial_ack","final":False,"body":initial},ensure_ascii=False)+"\n")
        if on_connected:
            on_connected()
        audio_started = time.monotonic()
        segments.start_wall = time.time()
        async def receiver():
            nonlocal finished
            async for raw in ws:
                body, final = decode(raw)
                elapsed = time.monotonic() - audio_started
                with (output_dir / "volc-events.jsonl").open("a") as f:
                    f.write(json.dumps({"elapsed": elapsed, "final": final, "body": body}, ensure_ascii=False) + "\n")
                for row in segments.accept(body, elapsed):
                    if on_segment:
                        on_segment(row)
                if final:
                    finished = True
                    return
            if not finished:
                raise RuntimeError("ASR connection ended without final acknowledgement")
        receiver_task = asyncio.create_task(receiver())
        async def sender():
            nonlocal sent
            seq = 2
            async for data in chunks:
                if len(data) % 2: raise ValueError("unaligned PCM16")
                await ws.send(packet(2, data, seq));sent += len(data);seq += 1
            await ws.send(packet(2, b"", seq, final=True))
        sender_task = asyncio.create_task(sender())
        try:
            done, _ = await asyncio.wait([sender_task,receiver_task],return_when=asyncio.FIRST_COMPLETED)
            if receiver_task in done:
                await receiver_task
                if not sender_task.done(): raise RuntimeError("ASR ended before all audio was sent")
            await sender_task
            await asyncio.wait_for(receiver_task,20)
        finally:
            for task in (sender_task,receiver_task):
                if not task.done(): task.cancel()
            await asyncio.gather(sender_task,receiver_task,return_exceptions=True)
    rows = segments.all()
    result = {"status": "PASS" if rows and finished else "NO_FINAL_TRANSCRIPT",
              "audio_seconds": round(sent / 32000, 3), "wall_seconds": round(time.monotonic() - started, 3),
              "initialization_seconds": round(audio_started-started,3),
              "latency_basis":"audio-feed-start; real-time file replay, not live microphone latency",
              "first_partial_seconds": segments.first_partial_seconds,
              "first_final_seconds": segments.first_final_seconds,
              "sentence_end_to_final_seconds": segments.final_latencies,
              "anonymous_speaker_labels": sorted({u["speaker"] for u in rows}),
              "utterance_count": len(rows), "final_acknowledged": finished,
              "request_id": req_id, "log_id": ws.response.headers.get("x-tt-logid"),
              "asr_resource": config["asr_resource"]}
    (output_dir / "transcripts.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    (output_dir / "asr-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return rows, result

async def wav_chunks(path, realtime=True):
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2 or wav.getnchannels() != 1:
            raise ValueError("requires mono PCM16 WAV")
        rate = wav.getframerate()
        state = None
        started, samples = time.monotonic(), 0
        while True:
            data = wav.readframes(int(rate * .2))
            if not data:
                break
            samples += len(data) // 2
            if realtime:
                await asyncio.sleep(max(0, started + samples / rate - time.monotonic()))
            if rate != 16000:
                data, state = audioop.ratecv(data, 2, 1, rate, 16000, state)
            yield data

class VolcChannel:
    """Existing meeting service adapter; preserves Mac's 24 kHz wire format."""
    def __init__(self, run, source, key, memory, max_seconds):
        self.run, self.source, self.key, self.memory, self.max_seconds = Path(run), source, key, memory, max_seconds

    async def run_stream(self):
        from .resilient_asr import run_channel
        return await run_channel(self)
