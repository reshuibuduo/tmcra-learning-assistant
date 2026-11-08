// Incremental fetch/SSE reader. One click owns one stream; there is no batch fallback.
export class SSEDecoder{
  constructor(){this.buffer='';}
  feed(text){
    this.buffer+=text;if(this.buffer.length>131072)throw Error('回答数据过长。');
    const events=[];let match;
    while((match=/\r?\n\r?\n/.exec(this.buffer))){
      const frame=this.buffer.slice(0,match.index);this.buffer=this.buffer.slice(match.index+match[0].length);
      const data=frame.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).replace(/^ /,'')).join('\n');
      if(data)events.push(JSON.parse(data));
    }
    return events;
  }
}

export async function readQuestionStream(fetchImpl,path,body,token,onEvent,{signal,timeoutMs=45000}={}){
  const controller=new AbortController();let timedOut=false,reader=null,completed=false,answer=null;
  const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)abort();
  const timer=setTimeout(()=>{timedOut=true;controller.abort();},timeoutMs);
  try{
    const response=await fetchImpl(path,{method:'POST',headers:{'Content-Type':'application/json','Accept':'text/event-stream',Authorization:'Bearer '+token},body:JSON.stringify(body),signal:controller.signal});
    if(!response.ok){let data={};try{data=await response.json();}catch{}throw Error(response.status===401?'连接已过期，请重新连接问答窗口。':typeof data.detail==='string'?data.detail:'问答请求未被接受。');}
    if(!response.headers.get('content-type')?.includes('text/event-stream')||!response.body)throw Error('服务没有返回流式回答。');
    reader=response.body.getReader();const decoder=new TextDecoder('utf-8',{fatal:true}),sse=new SSEDecoder();
    while(!completed){
      const chunk=await reader.read();
      const text=chunk.done?decoder.decode():decoder.decode(chunk.value,{stream:true});
      for(const event of sse.feed(text)){
        if(event.request_id!==body.request_id)throw Error('回答编号不匹配。');
        if(event.type==='error'){const error=Error(event.message||'回答流中断。');error.code=event.code;throw error;}
        if(!['start','answer_delta','example_delta','done'].includes(event.type))throw Error('回答流格式异常。');
        if(event.type.endsWith('_delta')&&typeof event.text!=='string')throw Error('回答片段格式异常。');
        if(event.type==='done'){
          if(!event.answer||event.answer.request_id!==body.request_id||typeof event.answer.answer!=='string'||!Array.isArray(event.answer.citations))throw Error('完整回答格式异常。');
          answer=event.answer;completed=true;
        }
        onEvent(event);
        if(completed)break;
      }
      if(chunk.done&&!completed)throw Error('回答流提前结束，内容尚未完整。');
    }
    return answer;
  }catch(error){if(timedOut)throw Error('本次回答超时，内容尚未完整。');throw error;}
  finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);if(reader){try{await reader.cancel();}catch{}reader.releaseLock();}}
}

// The first post-paint frame is an estimate of visibility, not a hardware timestamp.
export function createStreamTiming({now,afterPaint,onUpdate,visibility}){
  const started=now(),state={updates:0,cached:false,visibility:visibility()};let firstScheduled=false;
  const elapsed=()=>Math.round((now()-started)*10)/10;
  return {state,
    cached(value){state.cached=!!value;onUpdate(state);},
    delta(text){
      if(!text)return;state.updates++;
      if(!firstScheduled&&text.trim()){
        firstScheduled=true;state.first_text_ms=elapsed();
        afterPaint(()=>{state.first_display_ms=elapsed();onUpdate(state);});
      }
      onUpdate(state);
    },
    done(){state.complete_received_ms=elapsed();afterPaint(()=>{state.complete_display_ms=elapsed();onUpdate(state);});},
  };
}
