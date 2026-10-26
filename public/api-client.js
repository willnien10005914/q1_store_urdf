import {zipStored} from './zip.js';
import {validateSegment} from './clip-editor.js';
import {tr,L} from './i18n.js';
/** Native Python API when available; explicit browser-local demo on static hosting. */
let local=false,catalog;
const key='q1-store-v1';
function read(){const defaults={jobs:{},motions:{},listings:[],purchases:[],robots:[],deployments:[]};try{return {...defaults,...(JSON.parse(localStorage.getItem(key))||{})};}catch{return defaults;}}
function save(data){localStorage.setItem(key,JSON.stringify(data));}
function uid(){return crypto.randomUUID().replaceAll('-','');}
const library=[['rsl_rl','RSL-RL · PPO'],['skrl','skrl · PPO'],['rl_games','RL-Games · PPO'],['sb3','Stable-Baselines3 · PPO'],['torchrl','TorchRL · PPO']];
const physics=[['isaacsim_physx','Isaac Sim · PhysX'],['newton_mjwarp','Newton · MuJoCo Warp'],['ovphysx','OV PhysX · standalone']];
const rewards=[['motion_tracking','動作追蹤','Motion tracking'],['joint_position','關節位置','Joint position'],['balance','平衡與姿態','Balance & posture'],['energy','能耗與力矩','Energy & torque'],['smoothness','動作平滑','Smoothness'],['foot_contact','足部接觸','Foot contact'],['wheel_velocity','輪速追蹤','Wheel velocity tracking']];
const landmarks=[['taipei101','台北 101 · Taipei','Taipei 101 · Taipei'],['eiffel','艾菲爾鐵塔 · Paris','Eiffel Tower · Paris'],['times-square','時代廣場 · New York','Times Square · New York'],['shibuya','澀谷路口 · Tokyo','Shibuya Crossing · Tokyo'],['great-wall','萬里長城 · Beijing','Great Wall · Beijing']];
const options=a=>a.map(([id,label,label_en])=>label_en?{id,label,label_en}:{id,label});
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
 if(!local){const result=await request(path,body);const normalize=job=>{if(!job||typeof job!=='object'||!job.id||!job.mode)return job;job.progress=(Number(job.progress)||0)/100;job.state=({training_demo:'training',rendering_demo:'rendering',demo_complete:'complete'})[job.state]||job.state;return job;};if(path.startsWith('/api/jobs')&&!path.endsWith('/plan')&&!path.endsWith('/log')){if(Array.isArray(result.jobs))result.jobs.forEach(normalize);else normalize(result);}return result;}
 const data=read();
 if(path==='/api/specs'){const response=await fetch('/assets/specs/catalog.json');if(!response.ok)throw Error(tr('規格資料不存在。','Spec data not found.'));const specs=await response.json();specs.trained=[];return specs;}
 if(path==='/api/jobs'&&!body)return {jobs:Object.values(data.jobs).map(advance).sort((a,b)=>b.created_at-a.created_at),worker:{online:false,state:'offline'}};
 if(path==='/api/jobs'&&body){let source=body.source,segment=body.segment;
 if(source.type==='video_example'){const item=catalog.video_examples?.find(v=>v.id===source.id);if(!item||item.robot_type!==body.robot_type)throw Error(tr('影片範例與機型不符。','Video example does not match the robot type.'));source={...item,type:'video_example',url:item.video_url,source_start_s:item.start_s,source_end_s:item.end_s};segment=segment||{start_s:0,end_s:item.end_s-item.start_s};}
 if(segment){segment=validateSegment(+segment.start_s,+segment.end_s,source.type==='video_example'?source.end_s-source.start_s:undefined);segment={...segment,duration_s:segment.end_s-segment.start_s,source_start_s:(source.source_start_s||0)+segment.start_s,source_end_s:(source.source_start_s||0)+segment.end_s};}
 const id=uid(),job={...body,source,segment,id,state:body.mode==='worker'?'queued_for_worker':'ingesting',progress:0,elapsed_s:0,created_at:Date.now()/1000,result:null,browser_local:true};job.plan=plan(job);data.jobs[id]=job;save(data);return job;}
 if(path.startsWith('/api/jobs/')){const id=path.split('/')[3],job=data.jobs[id];if(!job)throw Error(tr('找不到工作','Job not found'));if(path.endsWith('/cancel'))job.state='cancelled';advance(job);save(data);return path.endsWith('/plan')?plan(job):path.endsWith('/log')?{id,state:job.state,log_tail:[]}:job;}
 if(path==='/api/motions'&&body){const id=uid();data.motions[id]={id,...body};save(data);return {id};}
 if(path==='/api/robots'&&!body)return {robots:data.robots};
 if(path==='/api/robots'&&body){const name=String(body.name||'').trim(),serial=String(body.serial||'').trim(),robot=catalog.robots.find(r=>r.id===body.robot_type);if(!robot||!name||name.length>60||!serial||serial.length>80||!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$/.test(serial)||/[\u0000-\u001f]/.test(name))throw Error(tr('請輸入有效的機型、名稱與序號。','Enter a valid robot type, name and serial.'));if(data.robots.some(r=>r.serial.toLowerCase()===serial.toLowerCase()))throw Error(tr('這個序號已註冊。','This serial is already registered.'));const registered={id:uid(),name,serial,robot_type:robot.id,urdf_sha256:robot.urdf_sha256,control_joints:robot.control_joints,connection_state:'not_connected',hardware_delivery:false,created_at:Date.now()/1000};data.robots.push(registered);save(data);return registered;}
 if(path==='/api/deployments'&&!body)return {deployments:data.deployments};
 if(path==='/api/deployments'&&body){const job=data.jobs[body.job_id],robot=data.robots.find(r=>r.id===body.robot_id);if(!job||advance(job).state!=='complete')throw Error(tr('請先完成動作工作。','Finish the motion job first.'));if(!robot||robot.robot_type!==job.robot_type)throw Error(tr('註冊機器人與工作機型不符。','Registered robot does not match the job robot type.'));const current=catalog.robots.find(r=>r.id===robot.robot_type);if(robot.urdf_sha256!==current.urdf_sha256||job.plan?.robot?.urdf_sha256!==current.urdf_sha256)throw Error(tr('機器人或工作的 URDF 版本已變更。','The robot or job URDF version has changed.'));if(JSON.stringify(robot.control_joints)!==JSON.stringify(current.control_joints))throw Error(tr('註冊機器人的控制關節契約已變更。','The registered robot control-joint contract has changed.'));const deployment={id:uid(),job_id:job.id,robot_id:robot.id,robot_type:robot.robot_type,target_robot:{...robot},status:'package_ready',package_kind:'preview_only',deployable:false,hardware_activation_allowed:false,checkpoint_included:false,device_acknowledged:false,transport:'browser_download',created_at:Date.now()/1000};deployment.download_url='browser-local:'+deployment.id;data.deployments.push(deployment);save(data);return deployment;}
 if(path==='/api/listings'&&!body)return {listings:data.listings};
 if(path==='/api/listings'&&body){const job=data.jobs[body.job_id];if(!job||advance(job).state!=='complete')throw Error(tr('請先完成 demo。','Finish the demo first.'));const listing={...body,id:uid(),robot_type:job.robot_type,video_url:job.result.video_url,provenance:job.result.provenance,newly_trained:false,demo:true,created_at:Date.now()/1000};data.listings.unshift(listing);save(data);return listing;}
 if(path==='/api/purchases'&&body){const listing=data.listings.find(l=>l.id===body.listing_id);if(!listing)throw Error(tr('找不到作品','Listing not found'));const receipt={id:uid(),listing_id:listing.id,amount:listing.price,demo:true,charged:false};data.purchases.push(receipt);save(data);return receipt;}
 throw Error(tr('此功能需要本機 Python 服務。','This feature needs the local Python service.'));
}
export async function uploadVideo(file){
 if(local)return {id:uid(),url:URL.createObjectURL(file),filename:file.name,type:'upload',browser_local:true};
 const r=await fetch('/api/uploads',{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream','X-Filename':encodeURIComponent(file.name)},body:file});const d=await r.json();if(!r.ok)throw Error(d.error||tr('影片上傳失敗','Video upload failed'));return d;
}
export async function downloadPlan(job){const data=await api('/api/jobs/'+job.id+'/plan');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));return url;}

export async function downloadDeployment(deployment){
 let blob;
 if(local){const data=read(),job=data.jobs[deployment.job_id],motion=data.motions[job.motion_id],reference=catalog.robots.find(r=>r.id===deployment.robot_type);const manifest={schema:'q1-store.deployment.v1',...deployment,policy_format:null,checkpoint:null,scope:'Preview-only configuration package; no runnable policy and no hardware activation.'};const files={'manifest.json':JSON.stringify(manifest,null,2),'job-plan.json':JSON.stringify(plan(job),null,2),'robot-contract.json':JSON.stringify(reference,null,2),'README.txt':'Q1 preview deployment package\nTarget: '+deployment.target_robot.name+' / '+deployment.target_robot.serial+'\nNo checkpoint is included. This package does not activate hardware.\nBrowser download is not a robot delivery acknowledgment. Use a compatible robot agent and a verified policy for actual deployment.\n'};if(motion)files['motion.json']=JSON.stringify(motion);blob=zipStored(files);}
 else{const response=await fetch(deployment.download_url);if(!response.ok){const error=await response.json();throw Error(error.error||tr('套件下載失敗。','Package download failed.'));}blob=await response.blob();}
 const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='q1-'+deployment.robot_type+'-'+deployment.id.slice(0,12)+'-preview.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return {size:blob.size,filename:a.download};
}
