import {tr,L} from './i18n.js';
const COLORS=['#00a77a','#7661e8','#f29832','#e3517c'];
const CONNECTIONS=[[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,31],[28,32]];
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const angle=(a,b,c)=>{const u=[a.x-b.x,a.y-b.y,(a.z||0)-(b.z||0)],v=[c.x-b.x,c.y-b.y,(c.z||0)-(b.z||0)];return Math.acos(clamp(u.reduce((s,x,i)=>s+x*v[i],0)/(Math.hypot(...u)*Math.hypot(...v)||1),-1,1));};

/** Resolve passive followers from the native URDF constraint, never independent targets. */
export function resolveNativeMimics(values,robot='leg'){
  const robotType=typeof robot==='string'?robot:robot.id;
  const fallback=[...['left','right'].map(side=>({name:`${side}_finger_2_Roll_joint`,type:'revolute',mimic:{joint:`${side}_finger_1_Roll_joint`,multiplier:-1,offset:0}})),
    ...(robotType==='wheel'?['left','right'].map(side=>({name:`${side}_knee_2_Pitch_joint`,type:'revolute',mimic:{joint:`${side}_knee_1_Pitch_joint`,multiplier:.444444,offset:0}})):[])];
  const definitions=typeof robot==='object'&&Array.isArray(robot.joints)?robot.joints:fallback;
  const byName=new Map(definitions.map(j=>[j.name,j])),q={...values};
  for(const j of definitions){
    if(j.type==='fixed')continue;
    const value=q[j.name]??(typeof robot==='object'?robot.default_joint_positions?.[j.name]:0)??0;
    q[j.name]=j.type==='continuous'?value:clamp(value,Number.isFinite(j.lower)?j.lower:-Infinity,Number.isFinite(j.upper)?j.upper:Infinity);
  }
  const resolved=new Set(),resolving=new Set();
  function resolve(name){
    if(resolved.has(name))return q[name]??0;
    if(resolving.has(name))throw new Error('Native URDF mimic cycle: '+name);
    resolving.add(name);const mimic=byName.get(name)?.mimic;
    if(mimic)q[name]=resolve(mimic.joint)*(mimic.multiplier??1)+(mimic.offset??0);
    resolving.delete(name);resolved.add(name);return q[name]??0;
  }
  for(const j of definitions)if(j.mimic)resolve(j.name);
  return q;
}

/** Geometric imitation reference in native joint axes; not inverse dynamics or a trained policy. */
export function mapLandmarks(p,robot='leg'){
  if(!p||p.length<33)return {};
  const robotType=typeof robot==='string'?robot:(robot.id||robot.robot_type||'leg'),q=typeof robot==='object'?{...robot.default_joint_positions}:{};
  const hips={x:(p[23].x+p[24].x)/2,y:(p[23].y+p[24].y)/2,z:((p[23].z||0)+(p[24].z||0))/2};
  const shoulders={x:(p[11].x+p[12].x)/2,y:(p[11].y+p[12].y)/2,z:((p[11].z||0)+(p[12].z||0))/2};
  q.center_waist_Roll_joint=clamp(-Math.atan2(shoulders.x-hips.x,hips.y-shoulders.y),-.2,.2);
  q.center_waist_Pitch_joint=clamp(Math.atan2(hips.z-shoulders.z,hips.y-shoulders.y),0,.45);
  q.center_waist_Yaw_joint=clamp(Math.atan2((p[12].z||0)-(p[11].z||0),Math.abs(p[12].x-p[11].x)||.1),-.5,.5);
  for(const [side,s,a,e,w,h,k,f]of [['left',1,11,13,15,23,25,27],['right',-1,12,14,16,24,26,28]]){
    const armDown=p[e].y-p[a].y,armOut=(p[e].x-p[a].x)*-s;
    q[`${side}_shoulder_Roll_joint`]=s*clamp(Math.atan2(Math.max(0,armOut),armDown),0,2.3);
    q[`${side}_shoulder_Pitch_joint`]=s*clamp(Math.atan2((p[a].z||0)-(p[e].z||0),Math.max(.05,armDown)),-.9,1.9);
    q[`${side}_shoulder_Yaw_joint`]=0;
    q[`${side}_elbow_Pitch_joint`]=-s*clamp(Math.PI-angle(p[a],p[e],p[w]),0,2.4);
    q[`${side}_hip_Pitch_joint`]=s*clamp(Math.atan2((p[h].z||0)-(p[k].z||0),Math.max(.05,p[k].y-p[h].y)),-.6,.9);
    q[`${side}_hip_Roll_joint`]=s*clamp(Math.atan2(Math.abs(p[k].x-p[h].x),Math.max(.05,p[k].y-p[h].y)),0,.45);
    const bend=clamp(Math.PI-angle(p[h],p[k],p[f]),0,1.5);
    if(robotType==='wheel'){
      q[`${side}_knee_1_Pitch_joint`]=-s*(.28+bend*.55);
      q[`${side}_wheel_Pitch_joint`]=0;
    }else{
      q[`${side}_knee_Pitch_joint`]=s*bend;
      q[`${side}_ankle_Pitch_joint`]=-s*clamp(bend*.45,-.5,.5);
      q[`${side}_ankle_Roll_joint`]=-s*clamp(q[`${side}_hip_Roll_joint`]*.3,-.3,.3);
    }
  }
  return resolveNativeMimics(q,robot);
}

function demoLandmarks(t,offset=0){
  const cx=.34+offset,cy=.44,scale=.58,p=Array.from({length:33},()=>({x:cx,y:cy,z:0,visibility:.98}));
  const put=(i,x,y,z=0)=>p[i]={x:cx+x*scale,y:cy+y*scale,z,visibility:.98};
  put(0,0,-.35);put(11,-.13,-.2);put(12,.13,-.2);put(23,-.09,.13);put(24,.09,.13);
  const a=Math.sin(t*2.2+offset*6),b=Math.cos(t*2.2+offset*6);
  put(13,-.2-.06*a,-.06-.13*a,-.1);put(15,-.33-.08*a,-.15-.13*a,-.25*(a+1));
  put(14,.19+.07*b,-.08-.1*b,-.12);put(16,.3+.1*b,-.19-.1*b,-.2*(b+1));
  put(25,-.18-.03*a,.32,-.06);put(27,-.21,.57);put(26,.18+.03*a,.34);put(28,.24,.57);put(31,-.25,.6);put(32,.28,.6);
  return p;
}

/** Validate local playable-video bounds; original YouTube offsets belong to source metadata. */
export function normalizeVideoSegment(segment,duration){
  if(!segment||typeof segment!=='object'||Array.isArray(segment))throw new Error(tr('請選擇影片片段的起訖時間。','Choose the start and end time of the video segment.'));
  const {start_s,end_s}=segment;
  if(typeof start_s!=='number'||typeof end_s!=='number'||!Number.isFinite(start_s)||!Number.isFinite(end_s))throw new Error(tr('片段時間必須是有限秒數。','Segment times must be finite seconds.'));
  if(!Number.isFinite(duration)||duration<=0)throw new Error(tr('請先載入可播放的影片與長度。','Load a playable video with a known duration first.'));
  if(start_s<0||end_s-start_s<.5-1e-8||end_s-start_s>60+1e-8||end_s>duration+1e-6)throw new Error(tr('請選擇影片內0.5至60秒的有效片段。','Choose a valid 0.5–60 s segment within the video.'));
  return {start_s,end_s:Math.min(end_s,duration)};
}

/** Multi-person tracking with a stable colour per nearest-hip track. Explicit demo mode is procedural. */
export function createRetargetController({video,overlay,onPeople=()=>{},onFrame=()=>{},onStatus=()=>{},poseDetector=null}){
  const ctx=overlay.getContext('2d');let running=false,raf=0,lastInference=-Infinity,lastVideo=-1,detector=poseDetector,selected=null,tracks=[],nextTrack=0,frames=[],robot='leg',demo=false,startAt=0,captureStartAt=0,session=0,smoothed={},segment=null,originalLoop=null,requireTargetSelection=false;
  function selectTarget(id){if(id!==selected){selected=id;frames=[];smoothed={};captureStartAt=performance.now();}}
  function size(){const r=overlay.getBoundingClientRect();overlay.width=Math.max(1,Math.round(r.width*devicePixelRatio));overlay.height=Math.max(1,Math.round(r.height*devicePixelRatio));}
  function draw(people){
    size();ctx.clearRect(0,0,overlay.width,overlay.height);
    const width=overlay.width,height=overlay.height;
    const videoRatio=video.videoWidth/Math.max(1,video.videoHeight),viewRatio=width/height;
    let dw=width,dh=height,ox=0,oy=0;
    if(!demo&&Number.isFinite(videoRatio)&&videoRatio>0){if(videoRatio>viewRatio){dh=width/videoRatio;oy=(height-dh)/2;}else{dw=height*videoRatio;ox=(width-dw)/2;}}
    for(const person of people){
      const pts=person.landmarks,chosen=person.id===selected;ctx.strokeStyle=person.color;ctx.fillStyle=person.color;ctx.globalAlpha=chosen?1:.53;ctx.lineWidth=(chosen?3:2)*devicePixelRatio;
      for(const [a,b]of CONNECTIONS){if((pts[a].visibility??1)<.35||(pts[b].visibility??1)<.35)continue;ctx.beginPath();ctx.moveTo(ox+pts[a].x*dw,oy+pts[a].y*dh);ctx.lineTo(ox+pts[b].x*dw,oy+pts[b].y*dh);ctx.stroke();}
      for(const i of [0,11,12,13,14,15,16,23,24,25,26,27,28]){const p=pts[i];if((p.visibility??1)<.35)continue;ctx.beginPath();ctx.arc(ox+p.x*dw,oy+p.y*dh,(chosen?3.7:2.7)*devicePixelRatio,0,Math.PI*2);ctx.fill();}
      const minx=Math.min(...[11,12,23,24].map(i=>pts[i].x)),miny=Math.min(pts[0].y,pts[11].y,pts[12].y)-.065;
      ctx.font=`600 ${12*devicePixelRatio}px system-ui`;ctx.fillText(`${person.label}${chosen?' · TARGET':''}`,ox+minx*dw,oy+miny*dh);
    }
    ctx.globalAlpha=1;
  }
  function identify(landmarks,worlds=[]){
    const now=performance.now(),remaining=tracks.filter(t=>now-t.seen<1800),used=new Set(),people=[];
    for(let index=0;index<landmarks.length;index++){
      const p=landmarks[index],hip={x:(p[23].x+p[24].x)/2,y:(p[23].y+p[24].y)/2};
      let best=null,distance=.21;
      for(const track of remaining){if(used.has(track.id))continue;const d=Math.hypot(hip.x-track.hip.x,hip.y-track.hip.y);if(d<distance){distance=d;best=track;}}
      if(!best){const n=nextTrack++;best={id:'person-'+n,color:COLORS[n%COLORS.length],label:(requireTargetSelection?tr('候選 ','Candidate '):tr('人物 ','Person '))+(n+1)};}
      used.add(best.id);people.push({...best,index,hip,seen:now,landmarks:p,world:worlds[index],confidence:p.reduce((s,v)=>s+(v.visibility??1),0)/p.length});
    }
    tracks=people.concat(remaining.filter(t=>!used.has(t.id)));
    if(selected===null&&people.length&&!requireTargetSelection)selectTarget(people[0].id);
    return people;
  }
  function deliver(people,t){
    onPeople(people.map(({id,index,color,label,confidence})=>({id,index,color,label,confidence,selected:id===selected})));
    draw(people);const target=people.find(p=>p.id===selected);
    if(!target){
      if(requireTargetSelection&&selected===null&&people.length)video.pause();
      onStatus(requireTargetSelection&&selected===null?(people.length?tr('影片已暫停 · 請確認來源機器人並點選候選目標繼續；若只有背景人物，可保留為參考工作。','Video paused · confirm the source robot and click a candidate to continue; if only background people are present, keep it as a reference job.'):tr('等待來源機器人的候選姿態；尚未選取目標，不輸出關節。','Waiting for a candidate pose of the source robot; no target selected, no joints output.')):tr('選取的目標暫時離開畫面；暫停輸出關節。','The selected target left the frame; joint output paused.'));return;
    }
    const raw=mapLandmarks(target.landmarks,robot),joints={};
    for(const [k,v]of Object.entries(raw)){smoothed[k]=(smoothed[k]??v)*.55+v*.45;joints[k]=smoothed[k];}
    const payload={time:t,captured_time:(performance.now()-captureStartAt)/1000,joints,person:target.id,landmarks:target.landmarks,confidence:target.confidence,demo};
    if(frames.length<18000)frames.push(payload);
    onFrame(payload);
  }
  function finishSegment(){
    running=false;cancelAnimationFrame(raf);video.pause();if(originalLoop!==null){video.loop=originalLoop;originalLoop=null;}
    onStatus(requireTargetSelection&&selected===null?tr('片段檢視完成 · 沒有已確認的來源機器人；可保留片段交由專用追蹤 adapter 核對。','Segment review complete · no confirmed source robot; keep the segment for a dedicated tracking adapter.'):frames.length>=2?tr('片段辨識完成 · ','Segment recognition complete · ')+frames.length+tr('個影格；請檢查追蹤目標與關節映射。',' frames; check the tracking target and joint mapping.'):tr('片段辨識完成 · 有效影格不足2個；請重選目標，或以人工標記／機器人追蹤 adapter 建立參考。','Segment recognition complete · fewer than 2 valid frames; reselect the target or build a reference with manual labels / a robot tracking adapter.'));
  }
  function tick(now){
    if(!running)return;
    if(!demo&&segment&&(video.currentTime>=segment.end_s||video.ended)){finishSegment();return;}
    raf=requestAnimationFrame(tick);
    if(now-lastInference<90)return;
    if(!demo&&(video.readyState<2||video.paused||video.currentTime===lastVideo))return;
    if(!demo&&segment&&video.currentTime<segment.start_s)return;
    lastInference=now;lastVideo=video.currentTime;
    try{
      if(demo){
        const t=(now-startAt)/1000;
        if(selected===null)selectTarget('person-0');
        // Demo identities are authored roles, independent of real detector timeouts.
        deliver([0,1].map(index=>({id:'person-'+index,index,color:COLORS[index],label:tr('人物 ','Person ')+(index+1),landmarks:demoLandmarks(t,index*.34),confidence:.98})),t);
      }
      else{const result=detector.detectForVideo(video,now);deliver(identify(result.landmarks,result.worldLandmarks),video.currentTime);}
    }catch(error){stop();onStatus(tr('MediaPipe 偵測停止：','MediaPipe detection stopped: ')+error.message);}
  }
  function stop(){running=false;session++;cancelAnimationFrame(raf);if(originalLoop!==null){video.loop=originalLoop;originalLoop=null;}ctx.clearRect(0,0,overlay.width,overlay.height);return api;}
  function resetCapture(){
    stop();frames=[];selected=null;tracks=[];nextTrack=0;smoothed={};demo=false;segment=null;requireTargetSelection=false;
    lastVideo=-1;lastInference=-Infinity;startAt=captureStartAt=performance.now();onPeople([]);return api;
  }
  async function seekToStart(){
    if(!segment||Math.abs(video.currentTime-segment.start_s)<.001)return;
    if(typeof video.addEventListener!=='function'){video.currentTime=segment.start_s;return;}
    await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>finish(new Error(tr('影片定位逾時，請重新載入影片。','Video seek timed out; reload the video.'))),8000);
      function finish(error){clearTimeout(timeout);video.removeEventListener('seeked',done);video.removeEventListener('error',failed);error?reject(error):resolve();}
      const done=()=>finish(),failed=()=>finish(new Error(tr('無法定位影片片段。','Cannot seek to the video segment.')));
      video.addEventListener('seeked',done,{once:true});video.addEventListener('error',failed,{once:true});video.currentTime=segment.start_s;
    });
  }
  async function start(options={}){
    stop();const token=++session;demo=options.demo===true;requireTargetSelection=!demo&&options.requireTargetSelection===true;robot=options.robot||robot;tracks=[];selected=null;frames=[];nextTrack=0;smoothed={};lastVideo=-1;lastInference=-Infinity;
    segment=!demo&&options.segment?normalizeVideoSegment(options.segment,video.duration):null;
    if(!demo){if(video.readyState<2)throw new Error(tr('請先載入可播放的影片。','Load a playable video first.'));video.pause();if(segment){originalLoop=video.loop;video.loop=false;}}
    if(!demo&&!detector){
      onStatus(tr('正在載入 MediaPipe Pose Landmarker（首次需要網路）…','Loading MediaPipe Pose Landmarker (network needed the first time)…'));
      try{
        const {FilesetResolver,PoseLandmarker}=await import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.32/vision_bundle.mjs');
        const vision=await FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.32/wasm');
        detector=await PoseLandmarker.createFromOptions(vision,{baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task',delegate:'CPU'},runningMode:'VIDEO',numPoses:4,minPoseDetectionConfidence:.45,minPosePresenceConfidence:.45,minTrackingConfidence:.45,outputSegmentationMasks:false});
      }catch(error){onStatus(tr('MediaPipe 載入失敗，請檢查網路後重試。','MediaPipe failed to load; check the network and retry.')+error.message);throw error;}
    }
    if(token!==session)return api;
    if(!demo){await seekToStart();if(token!==session)return api;await video.play();}
    running=true;startAt=captureStartAt=performance.now();onStatus(demo?tr('功夫動作示意 · 程序化骨架，非影片辨識結果','Kung fu motion sketch · procedural skeleton, not video recognition'):tr('MediaPipe 即時辨識 · 點選顏色追蹤目標','MediaPipe live recognition · click a color to track'));raf=requestAnimationFrame(tick);return api;
  }
  const api={start,stop,resetCapture,selectPerson(id){
    selectTarget(id);
    if(running&&requireTargetSelection&&video.paused){
      // Pausing for human review must not expire the candidate's tracking identity.
      const now=performance.now(),token=session,targetId=selected;tracks=tracks.map(track=>({...track,seen:now}));
      Promise.resolve(video.play()).then(()=>{if(running&&session===token&&selected===targetId)onStatus(tr('已選取候選目標 · 繼續片段辨識，請核對來源機器人與關節映射。','Candidate selected · continuing segment recognition; verify the source robot and joint mapping.'));}).catch(error=>{if(running&&session===token)onStatus(tr('目標已選取，請按播放繼續辨識：','Target selected; press play to continue recognition: ')+error.message);});
    }
    return api;
  },setRobot(value){robot=value;smoothed={};return api;},getFrames(){return frames.slice();},
    async exportMotion(value=robot){
      const robotType=typeof value==='string'?value:value.id;
      const targetFrames=frames.filter(f=>f.person===selected),origin=targetFrames[0]?.captured_time??0;
      const mapped=targetFrames.map(f=>({time:f.captured_time-origin,source_time:f.time,person:f.person,confidence:f.confidence,joints:mapLandmarks(f.landmarks,value)}));
      const jointNames=typeof value==='object'&&value.joints?value.joints.filter(j=>j.type!=='fixed').map(j=>j.name):Object.keys(mapped[0]?.joints||{});
      return {schema:1,source:demo?'procedural_kungfu_demo':'mediapipe_pose_landmarker',robot_type:robotType,urdf_sha256:typeof value==='object'?value.urdf_sha256:undefined,target_person:selected,demo,...(segment?{segment:{...segment}}:{}),physical_simulation:false,mapping:{method:'geometric_native_axes_v1',joint_limits_enforced:typeof value==='object',native_mimic_constraints:true,root_motion:false,wheel_policy:'stationary_reference',physical_validation:false},description:'Geometric retarget reference only; requires dynamics, contact, balance and task validation before training.',joint_names:jointNames,frames:mapped.map(f=>({...f,q:jointNames.map(name=>f.joints[name]??(typeof value==='object'?value.default_joint_positions?.[name]:0)??0)}))};
    },dispose(){stop();detector?.close();detector=null;}};
  return api;
}
