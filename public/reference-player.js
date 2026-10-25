/** Replay attributed image-space reference annotations with native-joint geometry. */
const BONES=[[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,31],[28,32]];
const timeOf=frame=>frame.source_time??frame.time;
export function sampleReference(reference,time){
 const frames=reference.frames;let low=0,high=frames.length-1;
 while(low<high-1){const mid=(low+high)>>1;if(timeOf(frames[mid])>time)high=mid;else low=mid;}
 const a=frames[low],b=frames[Math.min(low+1,frames.length-1)],mix=Math.max(0,Math.min(1,(time-timeOf(a))/Math.max(.000001,timeOf(b)-timeOf(a))));
 const q=a.q.map((value,i)=>value+(b.q[i]-value)*mix),landmarks=a.landmarks.map((point,i)=>({x:point.x+(b.landmarks[i].x-point.x)*mix,y:point.y+(b.landmarks[i].y-point.y)*mix,z:(point.z||0)+((b.landmarks[i].z||0)-(point.z||0))*mix,visibility:1}));
 return {q,landmarks,joints:Object.fromEntries(reference.joint_names.map((name,i)=>[name,q[i]]))};
}
export function sliceReference(reference,segment){
 const frames=reference.frames.filter(f=>timeOf(f)>=segment.start_s-1e-6&&timeOf(f)<=segment.end_s+1e-6),origin=frames.length?timeOf(frames[0]):0;
 return {...reference,segment:{...segment},target_person:'annotated-source-robot',physical_simulation:false,frames:frames.map(frame=>({...frame,time:timeOf(frame)-origin,source_time:timeOf(frame)}))};
}
export function createReferencePlayer({video,overlay,onFrame=()=>{},onStatus=()=>{}}){
 const context=overlay.getContext('2d');let reference=null,raf=0,playing=false,range=null,loop=true,session=0;
 function draw(landmarks){
  const rect=overlay.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),width=Math.max(1,Math.round(rect.width*dpr)),height=Math.max(1,Math.round(rect.height*dpr));if(overlay.width!==width)overlay.width=width;if(overlay.height!==height)overlay.height=height;
  context.clearRect(0,0,width,height);const ratio=video.videoWidth/Math.max(1,video.videoHeight),aspect=width/height;let dw=width,dh=height,ox=0,oy=0;if(ratio>aspect){dh=width/ratio;oy=(height-dh)/2;}else{dw=height*ratio;ox=(width-dw)/2;}
  context.strokeStyle='#00d2a0';context.fillStyle='#00d2a0';context.lineWidth=2.5*dpr;
  for(const [a,b]of BONES){context.beginPath();context.moveTo(ox+landmarks[a].x*dw,oy+landmarks[a].y*dh);context.lineTo(ox+landmarks[b].x*dw,oy+landmarks[b].y*dh);context.stroke();}
  for(const i of [0,11,12,13,14,15,16,23,24,25,26,27,28,31,32]){context.beginPath();context.arc(ox+landmarks[i].x*dw,oy+landmarks[i].y*dh,3*dpr,0,Math.PI*2);context.fill();}
  context.fillStyle='#ffffff';context.strokeStyle='#172438';context.lineWidth=3*dpr;context.font=`600 ${10*dpr}px sans-serif`;context.strokeText('33 POINT REFERENCE / MANUAL CORRECTION',12*dpr,22*dpr);context.fillText('33 POINT REFERENCE / MANUAL CORRECTION',12*dpr,22*dpr);
 }
 function update(){if(!reference)return;const sample=sampleReference(reference,video.currentTime);draw(sample.landmarks);onFrame({...sample,time:video.currentTime,person:'annotated-source-robot'});}
 function tick(){if(!playing||!reference||!range)return;raf=requestAnimationFrame(tick);if(video.currentTime>=range.end_s-.02||video.ended){if(loop){video.currentTime=range.start_s;video.play().catch(()=>{});}else{stop();onStatus('校正骨架參考播放完成。');return;}}update();}
 function stop(){session++;playing=false;cancelAnimationFrame(raf);video.pause();}
 return {load(value,robot){stop();if(value.robot_type!==robot.id||value.urdf_sha256&&value.urdf_sha256!==robot.urdf_sha256)throw Error('骨架參考與原生機型版本不符。');if(!Array.isArray(value.frames)||value.frames.length<2||!Array.isArray(value.joint_names))throw Error('骨架參考資料不完整。');for(const frame of value.frames)if(frame.landmarks?.length!==33||frame.q?.length!==value.joint_names.length)throw Error('骨架參考影格格式不正確。');reference=value;range=value.segment||{start_s:timeOf(value.frames[0]),end_s:timeOf(value.frames.at(-1))};return this;},async play(options={}){if(!reference)throw Error('請先載入骨架參考。');const token=++session;range=options.segment||range;loop=options.loop!==false;video.loop=false;video.muted=true;video.currentTime=range.start_s;await video.play();if(token!==session||!reference)return;playing=true;onStatus('MediaPipe 33 點格式 · 來源機器人人工校正參考；幾何映射，未做物理驗證。');update();raf=requestAnimationFrame(tick);},async seek(seconds){if(!Number.isFinite(seconds))throw Error('影片時間必須是有限秒數。');if(!reference)throw Error('請先載入骨架參考。');stop();const time=Math.max(range.start_s,Math.min(range.end_s,seconds));if(Math.abs(video.currentTime-time)>.0001)await new Promise((resolve,reject)=>{const timer=setTimeout(()=>{video.removeEventListener('seeked',done);reject(Error('骨架影片定位逾時。'));},8000);function done(){clearTimeout(timer);resolve();}video.addEventListener('seeked',done,{once:true});video.currentTime=time;});update();return time;},stop,clear(){stop();reference=null;range=null;context.clearRect(0,0,overlay.width,overlay.height);},hasReference(){return !!reference;},getMotion(segment=range){return reference?sliceReference(reference,segment):null;},dispose(){stop();reference=null;}};
}
