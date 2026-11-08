export function audioFrame(offset,pcm){
  if(!Number.isSafeInteger(offset)||offset<0||offset%2||!pcm.byteLength||pcm.byteLength%2||pcm.byteLength>48000)throw Error('音频分片格式异常。');
  const frame=new ArrayBuffer(8+pcm.byteLength);new DataView(frame).setBigUint64(0,BigInt(offset));new Uint8Array(frame,8).set(new Uint8Array(pcm));return frame;
}

// No getUserMedia, session creation or finish API: this object only transports saved PCM.
export class ReliableUpload{
  constructor({id,store,connect,onState=()=>{},sleep=ms=>new Promise(r=>setTimeout(r,ms)),now=()=>Date.now(),retryMs=500,ackTimeoutMs=8000}){
    Object.assign(this,{id,store,connect,onState,sleep,now,retryMs,ackTimeoutMs});this.disposed=false;this.task=null;this.ws=null;this.writes=Promise.resolve();
  }
  enqueue(pcm){this.writes=this.writes.then(()=>this.store.enqueue(this.id,pcm));return this.writes;}
  start(){if(!this.task)this.task=this.run();return this;}
  async finish(){await this.writes;await this.store.intent(this.id,'stopped');this.start();await this.task;}
  dispose(){this.disposed=true;this.ws?.close();}
  reconnect(){this.ws?.close();}
  async emit(state,extra={}){const m=await this.store.meta(this.id);this.onState({state,queuedBytes:m.next-m.acked,ackedBytes:m.acked,capturedBytes:m.next,manualStopped:m.intent==='stopped',...extra});}
  async run(){
    let attempt=0;
    while(!this.disposed){
      const before=await this.store.meta(this.id);
      if(before.intent==='stopped'&&before.next===before.acked){await this.emit('saved');return;}
      let ws,closed=false,readyResolve,readyReject,lastAck=this.now(),sent=before.acked,acked=before.acked,chain=Promise.resolve(),fault=null,timer;
      try{
        await this.emit(attempt?'reconnecting':'connecting');ws=await this.connect();this.ws=ws;
        const ready=new Promise((resolve,reject)=>{readyResolve=resolve;readyReject=reject;});
        timer=setTimeout(()=>readyReject(Error('音频连接握手超时。')),10000);
        const close=()=>{closed=true;readyReject(Error('音频连接已断开。'));};
        ws.onclose=close;ws.onerror=close;
        ws.onmessage=e=>{chain=chain.then(async()=>{
          const value=JSON.parse(e.data);
          if(value.type==='conflict')throw Error('服务器音频与本地缓存冲突；原始数据保留。');
          if(!['ready','ack'].includes(value.type))return;
          if(!Number.isSafeInteger(value.bytes)||value.bytes<acked)throw Error('音频确认顺序异常。');
          await this.store.ack(this.id,value.bytes);acked=value.bytes;lastAck=this.now();
          if(value.type==='ready'){sent=acked;readyResolve();}
          await this.emit('uploading');
        }).catch(e=>{fault=e;closed=true;readyReject(e);ws.close();});};
        await ready;clearTimeout(timer);attempt=0;
        while(!closed&&!this.disposed){
          const m=await this.store.meta(this.id);
          if(m.intent==='stopped'&&m.acked===m.next){await this.emit('saved');return;}
          if(sent>acked&&this.now()-lastAck>this.ackTimeoutMs){ws.close();throw Error('音频接收确认超时，正在补传。');}
          if(sent-acked<96000&&sent<m.next){
            const pcm=await this.store.read(this.id,sent,Math.min(48000,96000-(sent-acked)));
            if(!pcm.byteLength)throw Error('本地音频分片暂未就绪。');
            if(ws.readyState!==1)throw Error('音频连接已断开。');
            ws.send(audioFrame(sent,pcm));sent+=pcm.byteLength;
          }else await this.sleep(50);
        }
        if(fault)throw fault;
      }catch(error){
        await this.emit('reconnecting',{message:error.message});attempt++;
      }finally{
        clearTimeout(timer);if(ws){ws.onmessage=ws.onclose=ws.onerror=null;ws.close();}this.ws=null;
        await chain.catch(()=>{});
      }
      if(!this.disposed)await this.sleep(Math.min(5000,this.retryMs*2**Math.min(attempt,3)));
    }
    throw Error('上传连接已暂停；本地缓存保留。');
  }
}
