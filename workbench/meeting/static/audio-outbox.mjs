// Audio is committed locally before transmission and retained until a durable ACK.
export class AudioOutbox{
  constructor(name='tmcra-audio-outbox-v1',indexedDB=globalThis.indexedDB,keyRange=globalThis.IDBKeyRange){this.name=name;this.idb=indexedDB;this.range=keyRange;}
  async db(){
    if(this.connection)return this.connection;
    if(!this.idb)throw Error('浏览器没有提供本地音频缓存，请在 Chrome 中打开录音页。');
    this.connection=new Promise((resolve,reject)=>{const r=this.idb.open(this.name,1);
      r.onupgradeneeded=()=>{r.result.createObjectStore('sessions',{keyPath:'id'});r.result.createObjectStore('chunks',{keyPath:['id','offset']});};
      r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});return this.connection;
  }
  async transaction(names,mode,work){
    const db=await this.db();return new Promise((resolve,reject)=>{const tx=db.transaction(names,mode);let value,error;
      tx.oncomplete=()=>resolve(value);tx.onerror=tx.onabort=()=>reject(error||tx.error||Error('本地音频缓存写入失败。'));
      try{work(tx,x=>value=x,e=>{error=e;tx.abort();});}catch(e){error=e;tx.abort();}});
  }
  async open(id,base=0){
    if(!Number.isSafeInteger(base)||base<0||base%2)throw Error('音频起点异常。');
    return this.transaction(['sessions'],'readwrite',(tx,done)=>{const store=tx.objectStore('sessions'),q=store.get(id);q.onsuccess=()=>{
      const m=q.result||{id,base,next:base,acked:base,intent:'active',updatedAt:Date.now()};store.put(m);done(m);};});
  }
  async meta(id){return this.transaction(['sessions'],'readonly',(tx,done)=>{const q=tx.objectStore('sessions').get(id);q.onsuccess=()=>done(q.result);});}
  async list(){return this.transaction(['sessions'],'readonly',(tx,done)=>{const q=tx.objectStore('sessions').getAll();q.onsuccess=()=>done(q.result.filter(m=>m.next>m.acked));});}
  async intent(id,value){return this.transaction(['sessions'],'readwrite',(tx,done,fail)=>{const s=tx.objectStore('sessions'),q=s.get(id);q.onsuccess=()=>{const m=q.result;if(!m)return fail(Error('音频缓存会话缺失。'));m.intent=value;m.updatedAt=Date.now();s.put(m);done(m);};});}
  async enqueue(id,pcm){
    if(!(pcm instanceof ArrayBuffer)||!pcm.byteLength||pcm.byteLength%2||pcm.byteLength>48000)throw Error('音频分片格式异常。');
    return this.transaction(['sessions','chunks'],'readwrite',(tx,done,fail)=>{const s=tx.objectStore('sessions'),q=s.get(id);q.onsuccess=()=>{
      const m=q.result;if(!m||m.intent!=='active')return fail(Error('采集已停止，未添加新的音频。'));
      if(m.next-m.acked+pcm.byteLength>512*1024*1024)return fail(Error('待上传音频缓存已满；已有缓存仍保留。'));
      tx.objectStore('chunks').put({id,offset:m.next,data:pcm.slice(0)});m.next+=pcm.byteLength;m.updatedAt=Date.now();s.put(m);done(m);};});
  }
  async ack(id,offset){
    return this.transaction(['sessions','chunks'],'readwrite',(tx,done,fail)=>{const s=tx.objectStore('sessions'),q=s.get(id);q.onsuccess=()=>{
      const m=q.result;if(!m||!Number.isSafeInteger(offset)||offset<m.acked||offset>m.next||offset%2)return fail(Error('服务器音频长度与本地缓存不一致；缓存已保留。'));
      m.acked=offset;m.updatedAt=Date.now();s.put(m);
      const c=tx.objectStore('chunks').openCursor(this.range.bound([id,0],[id,offset]));c.onsuccess=()=>{const row=c.result;if(!row)return; if(row.value.offset+row.value.data.byteLength<=offset)row.delete();row.continue();};done(m);};});
  }
  async read(id,offset,max=48000){
    return this.transaction(['chunks'],'readonly',(tx,done,fail)=>{const parts=[];let length=0,next=offset;
      const q=tx.objectStore('chunks').openCursor(this.range.bound([id,0],[id,Number.MAX_SAFE_INTEGER]));
      const finish=()=>{const bytes=new Uint8Array(length);let i=0;for(const p of parts){bytes.set(p,i);i+=p.length;}done(bytes.buffer);};
      q.onsuccess=()=>{const c=q.result;if(!c||length>=max)return finish();const row=c.value,end=row.offset+row.data.byteLength;
        if(end<=next){c.continue();return;}if(row.offset>next)return fail(Error('本地音频分片不连续；缓存已保留。'));
        const piece=new Uint8Array(row.data,next-row.offset,Math.min(end-next,max-length));parts.push(piece);length+=piece.length;next+=piece.length;c.continue();};});
  }
}
