// Bounded requests; never retry writes or restart microphone capture implicitly.
export function connectionLabel(ready,problem=''){return ready?'服务已连接':problem?'连接中断':'尚未连接';}
export function captureLabel(status,localStopped=false){
 if(localStopped&&['recording','finishing'].includes(status))return '本机采集已停止';
 return ({recording:'采集中',finishing:'正在整理',completed:'已结束',interrupted:'采集已中断',error:'处理遇到问题'})[status]||status;
}
export async function requestJSON(fetcher,url,options={}, {timeoutMs=6000}={}){
 const controller=new AbortController();let timer,timedOut=false;
 const timeout=new Promise((_,reject)=>{timer=setTimeout(()=>{timedOut=true;controller.abort();reject(Error('服务请求超时，请检查服务器连接。'));},timeoutMs);});
 try{
  return await Promise.race([(async()=>{const response=await fetcher(url,{...options,signal:controller.signal});const data=await response.json();return {response,data};})(),timeout]);
 }catch(e){
  if(timedOut)throw Error('服务请求超时，请检查服务器连接。');
  if(e instanceof SyntaxError)throw Error('服务没有返回有效结果，请检查服务器连接。');
  throw Error('服务器连接已中断；已收到的记录保留，请重新连接后开始采集。');
 }finally{clearTimeout(timer);}
}
