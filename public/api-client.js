import {validateSegment} from './clip-editor.js';
/** Native Python API when available; explicit browser-local demo on static hosting. */
let local=false,catalog;
const key='q1-store-v1';
function read(){try{return JSON.parse(localStorage.getItem(key))||{jobs:{},motions:{},listings:[],purchases:[]};}catch{return {jobs:{},motions:{},listings:[],purchases:[]};}}
function save(data){localStorage.setItem(key,JSON.stringify(data));}
function uid(){return crypto.randomUUID().replaceAll('-','');}
const library=[['rsl_rl','RSL-RL · PPO'],['skrl','skrl · PPO'],['rl_games','RL-Games · PPO'],['sb3','Stable-Baselines3 · PPO'],['torchrl','TorchRL · PPO']];
const physics=[['isaacsim_physx','Isaac Sim · PhysX'],['newton_mjwarp','Newton · MuJoCo Warp'],['ovphysx','OV PhysX · standalone']];
const rewards=[['motion_tracking','動作追蹤'],['joint_position','關節位置'],['balance','平衡與姿態'],['energy','能耗與力矩'],['smoothness','動作平滑'],['foot_contact','足部接觸'],['wheel_velocity','輪速追蹤']];
const landmarks=[['taipei101','台北 101 · Taipei'],['eiffel','艾菲爾鐵塔 · Paris'],['times-square','時代廣場 · New York'],['shibuya','澀谷路口 · Tokyo'],['great-wall','萬里長城 · Beijing']];
const options=a=>a.map(([id,label])=>({id,label}));
export async function initAPI(){
 try{const response=await fetch('/api/catalog');if(!response.ok)throw Error();catalog=await response.json();if(!catalog.robots)throw Error();}
 catch{local=true;const [r,e,v]=await Promise.all([fetch('/assets/robots/manifest.json').then(r=>r.json()),fetch('/assets/examples/catalog.json').then(r=>r.json()),fetch('/assets/video-examples/catalog.json').then(r=>r.json()).catch(()=>({examples:[],sources:[]}))]);catalog={robots:r.robots,examples:e.examples,video_examples:v.examples,video_sources:v.sources,capabilities:{libraries:options(library),physics:options(physics),rewards:options(rewards),landmarks:options(landmarks),demo_duration_s:30,worker_available:false,youtube_ingest:false,upload:false}};}
 return {...catalog,browser_local:local};
}
export const isBrowserLocal=()=>local;
async function request(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(data.error||'HTTP '+r.status);return data;}
function advance(job){
 if(job.state==='cancelled'||job.mode==='worker')return job;
 const elapsed=(Date.now()-job.created_at*1000)/1000,p=Math.min(1,elapsed/30);
 job.elapsed_s=elapsed;job.progress=p;job.state=p>=1?'complete':p<.15?'ingesting':p<.4?'retargeting':p<.8?'training': 'rendering';
 if(p>=1){const e=catalog.examples.find(e=>e.robot_type===job.robot_type);job.result={video_url:e.video_url,example_id:e.id,provenance:e.provenance,newly_trained:false,landmark_rendered:false};}
 return job;
}
function plan(job){const motion=job.motion_id?read().motions[job.motion_id]:null;return {schema:1,job_id:job.id,mode:job.mode,robot_type:job.robot_type,source:job.source,motion_id:job.motion_id,target_person:job.target_person,config:job.config,segment:job.segment,action_name:job.action_name,motion_extraction_ready:!!motion&&!motion.demo,motion:motion?{id:motion.id,source:motion.source,demo:motion.demo,frame_count:motion.frames.length,segment:motion.segment,mapping:motion.mapping,storage:'browser_local',requires_export:true}:null,robot:catalog.robots.find(r=>r.id===job.robot_type),readiness:{q1_task_adapter_registered:false,landmark_asset_verified:false,worker_available:false},requirements:['Register a Q1 Isaac Lab 3 task adapter','Validate native joints, rewards, observation/action contracts and backend','Supply licensed USD landmark asset and evaluate checkpoint','Run actual play/render worker and preserve checkpoint/video evidence'],newly_trained:false};}
export async function api(path,body){
 if(!local){const result=await request(path,body);if(path.startsWith('/api/jobs')&&!path.endsWith('/plan')){result.progress=(Number(result.progress)||0)/100;result.state=({training_demo:'training',rendering_demo:'rendering',demo_complete:'complete'})[result.state]||result.state;}return result;}
 const data=read();
 if(path==='/api/jobs'&&body){let source=body.source,segment=body.segment;
 if(source.type==='video_example'){const item=catalog.video_examples?.find(v=>v.id===source.id);if(!item||item.robot_type!==body.robot_type)throw Error('影片範例與機型不符。');source={...item,type:'video_example',url:item.video_url,source_start_s:item.start_s,source_end_s:item.end_s};segment=segment||{start_s:0,end_s:item.end_s-item.start_s};}
 if(segment){segment=validateSegment(+segment.start_s,+segment.end_s,source.type==='video_example'?source.end_s-source.start_s:undefined);segment={...segment,duration_s:segment.end_s-segment.start_s,source_start_s:(source.source_start_s||0)+segment.start_s,source_end_s:(source.source_start_s||0)+segment.end_s};}
 const id=uid(),job={...body,source,segment,id,state:body.mode==='worker'?'queued_for_worker':'ingesting',progress:0,elapsed_s:0,created_at:Date.now()/1000,result:null,browser_local:true};job.plan=plan(job);data.jobs[id]=job;save(data);return job;}
 if(path.startsWith('/api/jobs/')){const id=path.split('/')[3],job=data.jobs[id];if(!job)throw Error('找不到工作');if(path.endsWith('/cancel'))job.state='cancelled';advance(job);save(data);return path.endsWith('/plan')?plan(job):job;}
 if(path==='/api/motions'&&body){const id=uid();data.motions[id]={id,...body};save(data);return {id};}
 if(path==='/api/listings'&&!body)return {listings:data.listings};
 if(path==='/api/listings'&&body){const job=data.jobs[body.job_id];if(!job||advance(job).state!=='complete')throw Error('請先完成 demo。');const listing={...body,id:uid(),robot_type:job.robot_type,video_url:job.result.video_url,provenance:job.result.provenance,newly_trained:false,demo:true,created_at:Date.now()/1000};data.listings.unshift(listing);save(data);return listing;}
 if(path==='/api/purchases'&&body){const listing=data.listings.find(l=>l.id===body.listing_id);if(!listing)throw Error('找不到作品');const receipt={id:uid(),listing_id:listing.id,amount:listing.price,demo:true,charged:false};data.purchases.push(receipt);save(data);return receipt;}
 throw Error('此功能需要本機 Python 服務。');
}
export async function uploadVideo(file){
 if(local)return {id:uid(),url:URL.createObjectURL(file),filename:file.name,type:'upload',browser_local:true};
 const r=await fetch('/api/uploads',{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});const d=await r.json();if(!r.ok)throw Error(d.error||'影片上傳失敗');return d;
}
export async function downloadPlan(job){const data=await api('/api/jobs/'+job.id+'/plan');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));return url;}
