import {PCMResampler,floatToPCM} from './audio-utils.mjs';
class PCMCollector extends AudioWorkletProcessor {
 constructor(){super();this.resampler=new PCMResampler(sampleRate);this.pending=new Float32Array(4800);this.used=0;this.port.onmessage=e=>{if(e.data==='flush'){this.flush();this.port.postMessage({flushed:true});}};}
 flush(){if(!this.used)return;const pcm=floatToPCM(this.pending.subarray(0,this.used));this.port.postMessage({pcm},[pcm]);this.used=0;}
 process(inputs){const channels=inputs[0];if(!channels?.length)return true;const mono=new Float32Array(channels[0].length);for(const ch of channels)for(let i=0;i<mono.length;i++)mono[i]+=ch[i]/channels.length;for(const value of this.resampler.push(mono)){this.pending[this.used++]=value;if(this.used===4800)this.flush();}return true;}
}
registerProcessor('tmcra-pcm',PCMCollector);
