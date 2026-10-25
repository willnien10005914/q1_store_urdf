/** Render source-aligned, manually reviewed MediaPipe-format references and Q1 GIFs.
 * npm install; PLAYWRIGHT_MODULE=/path/to/playwright node scripts/render_retarget_gifs.mjs
 * --references-only emits JSON without browser rendering. No automatic pose accuracy is claimed.
 */
import {readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import {mapLandmarks} from '../public/retarget.js';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const catalogPath=path.join(root,'assets/video-examples/catalog.json');
const catalog=JSON.parse(await readFile(catalogPath,'utf8'));
const {robots}=JSON.parse(await readFile(path.join(root,'assets/robots/manifest.json'),'utf8'));
const FPS=10;
// Each key is [local seconds, nose, image-left shoulder/elbow/wrist, image-right shoulder/elbow/wrist,
// image-left hip/knee/ankle, image-right hip/knee/ankle]. Coordinates are observed pixels in a 480x270
// source frame. For wheel robots, ankles mark wheel centres; wheel speed is deliberately not inferred.
// Image-space side convention follows this demo's existing geometric mapper, not calibrated anatomy.
const annotations={
 'h2x-guard':{end:2.96,keys:[
 [0,[60,55],[28,81],[5,104],[-12,98],[82,86],[96,113],[108,146],[43,135],[26,184],[7,241],[79,136],[75,185],[75,232]],
 [.67,[72,53],[43,80],[23,108],[3,129],[88,86],[100,116],[109,140],[54,140],[36,185],[10,240],[86,144],[84,184],[80,234]],
 [1,[104,65],[70,91],[71,116],[92,135],[109,92],[122,119],[112,135],[66,142],[31,183],[0,251],[101,151],[104,187],[92,235]],
 [1.33,[159,86],[124,104],[89,103],[52,111],[158,115],[145,145],[129,154],[111,148],[92,190],[52,245],[140,159],[129,203],[122,239]],
 [1.67,[203,55],[179,87],[155,118],[146,130],[212,93],[211,122],[193,137],[165,143],[133,178],[60,251],[195,149],[215,181],[225,220]],
 [2,[214,69],[196,96],[176,103],[192,92],[228,99],[256,94],[281,74],[209,157],[239,191],[254,240],[236,160],[217,204],[198,250]],
 [2.33,[247,74],[222,105],[197,112],[169,129],[264,109],[288,125],[313,139],[235,165],[222,196],[206,259],[265,166],[284,196],[318,242]],
 [2.96,[251,66],[229,94],[199,109],[175,124],[271,102],[289,132],[319,145],[236,161],[222,194],[209,258],[266,161],[286,196],[318,240]]]},
 'h2x-punch':{end:2.96,keys:[
 [0,[217,43],[193,77],[161,95],[131,103],[235,85],[260,104],[287,123],[191,155],[172,201],[134,258],[225,157],[227,204],[238,242]],
 [.67,[220,54],[201,83],[186,104],[177,111],[231,83],[241,117],[224,139],[202,159],[188,204],[181,257],[230,165],[230,207],[252,246]],
 [1,[231,59],[214,88],[200,117],[215,131],[243,94],[264,126],[285,131],[220,157],[207,198],[201,247],[248,165],[262,201],[283,245]],
 [1.33,[256,74],[235,98],[218,126],[240,133],[267,106],[286,132],[310,136],[248,173],[236,213],[226,259],[274,177],[286,220],[311,258]],
 [1.67,[220,74],[199,100],[195,125],[217,137],[226,98],[232,128],[208,146],[207,161],[205,193],[222,232],[237,164],[257,195],[288,207]],
 [2,[208,89],[196,112],[183,93],[192,62],[220,112],[228,94],[247,63],[205,170],[220,194],[206,235],[229,166],[241,172],[255,155]],
 [2.33,[213,78],[196,103],[190,63],[204,38],[220,109],[232,72],[223,41],[201,172],[204,213],[203,255],[230,171],[247,186],[283,172]],
 [2.96,[206,72],[193,86],[184,121],[165,144],[226,106],[210,141],[197,164],[218,165],[247,176],[281,178],[245,166],[255,205],[261,239]]]},
 'h2x-turn-kick':{end:1.92,keys:[
 [0,[272,73],[239,94],[205,132],[193,160],[263,98],[273,128],[292,152],[218,127],[236,83],[230,23],[232,132],[227,177],[235,236]],
 [.5,[219,122],[210,137],[197,160],[195,184],[233,116],[251,94],[285,70],[213,163],[195,190],[182,224],[232,149],[258,94],[322,43]],
 [1,[197,104],[186,122],[174,141],[176,150],[209,120],[215,139],[202,152],[210,163],[189,202],[188,261],[229,160],[248,202],[281,237]],
 [1.5,[190,95],[175,118],[161,138],[173,145],[203,119],[215,142],[199,151],[185,170],[181,207],[171,254],[211,170],[218,208],[230,256]],
 [1.92,[188,96],[173,117],[161,139],[175,146],[204,120],[217,141],[202,151],[184,171],[179,207],[169,254],[210,170],[219,209],[231,255]]]},
 'x2-slope':{end:3.96,keys:[
 [0,[112,69],[91,94],[100,117],[125,140],[117,91],[124,123],[138,151],[109,146],[137,178],[114,199],[128,152],[145,181],[126,218]],
 [1,[183,60],[163,82],[166,112],[176,140],[190,83],[199,113],[205,140],[173,140],[189,158],[173,175],[197,140],[203,157],[189,188]],
 [2,[228,45],[210,66],[212,96],[227,125],[239,69],[248,100],[255,131],[219,134],[229,152],[211,158],[242,136],[248,157],[231,176]],
 [3,[236,41],[220,62],[218,92],[224,124],[245,63],[252,93],[259,121],[227,124],[237,147],[225,174],[248,124],[261,145],[250,166]],
 [3.96,[235,41],[216,64],[207,90],[207,120],[253,64],[261,91],[273,111],[221,115],[225,151],[219,174],[248,116],[256,141],[258,154]]]},
 'x2-turn':{start:.2,end:2.88,keys:[
 [.2,[123,34],[103,54],[93,82],[92,112],[133,53],[149,82],[163,108],[110,103],[117,147],[108,171],[138,106],[155,135],[150,152]],
 [.5,[134,32],[115,56],[107,86],[110,121],[147,56],[162,84],[176,111],[124,115],[138,152],[124,179],[149,113],[175,139],[177,164]],
 [1,[190,22],[166,50],[160,84],[173,123],[207,48],[225,80],[239,109],[180,108],[195,154],[181,198],[204,112],[225,150],[219,169]],
 [1.5,[229,15],[197,46],[192,87],[203,135],[256,47],[275,80],[293,115],[212,117],[225,165],[212,213],[241,117],[268,158],[264,179]],
 [2,[280,26],[245,66],[224,100],[224,142],[304,71],[313,98],[325,155],[251,143],[242,184],[228,216],[284,149],[299,180],[288,214]],
 [2.88,[262,22],[223,58],[215,103],[206,148],[300,58],[316,104],[327,149],[244,138],[238,189],[236,217],[282,138],[290,189],[292,217]]]}

};
const POINTS=[0,11,13,15,12,14,16,23,25,27,24,26,28];
function landmarksAt(config,time){
 let right=config.keys.findIndex(k=>k[0]>time);if(right<0)right=config.keys.length-1;const left=Math.max(0,right-1),a=config.keys[left],b=config.keys[right],mix=right===left?0:Math.max(0,Math.min(1,(time-a[0])/(b[0]-a[0])));
 const points=Array.from({length:33},()=>({x:.5,y:.5,z:0,visibility:0}));
 for(let j=0;j<POINTS.length;j++){const x=(a[j+1][0]+(b[j+1][0]-a[j+1][0])*mix)/480,y=(a[j+1][1]+(b[j+1][1]-a[j+1][1])*mix)/270;points[POINTS[j]]={x,y,z:0,visibility:x>=0&&x<=1&&y>=0&&y<=1?.75:.15};}
 // Face and hand landmarks share the observed head/wrist anchors; they are not individually estimated.
 for(const i of [1,2,3,4,5,6,7,8,9,10])points[i]={...points[0],visibility:.15};
 for(const [anchor,indexes]of [[15,[17,19,21]],[16,[18,20,22]]])for(const i of indexes)points[i]={...points[anchor],visibility:.15};
 for(const [anchor,indexes]of [[27,[29,31]],[28,[30,32]]])for(const i of indexes)points[i]={...points[anchor],x:points[anchor].x+(i>=31?.018:0),y:points[anchor].y+.01,visibility:.5};
 return points;
}
const references=[];
for(const item of catalog.examples){
 const config=annotations[item.id],robot=robots.find(r=>r.id===item.robot_type),joint_names=robot.joints.filter(j=>j.type!=='fixed').map(j=>j.name);
 const frames=Array.from({length:Math.ceil((config.end-(config.start||0))*FPS)},(_,i)=>{const source_time=+((config.start||0)+i/FPS).toFixed(3),landmarks=landmarksAt(config,source_time),joints=mapLandmarks(landmarks,robot);return {time:+(source_time-(config.start||0)).toFixed(3),source_time,landmarks,joints,q:joint_names.map(name=>joints[name]??robot.default_joint_positions[name]??0)};});
 const reference={schema:1,id:item.id,source:'manual_corrected_mediapipe_reference',robot_type:item.robot_type,urdf_sha256:robot.urdf_sha256,segment:{start_s:config.start||0,end_s:config.end},joint_names,frames,demo:false,physical_simulation:false,newly_trained:false,target_person:'source-robot-manually-annotated',mapping:{method:'mediapipe_format_manual_keyframes',manual_correction:true,physical_validation:false,inference_verified:false,depth_estimation:'not_calibrated_zero_depth',root_motion:false,wheel_policy:'stationary_reference',native_mimic_constraints:true,joint_limits_enforced:true},provenance:{source_url:item.source_url,source_video_id:item.source_video_id,original_start_s:+(item.start_s+(config.start||0)).toFixed(3),original_end_s:+(item.start_s+config.end).toFixed(3),source_video_sha256:item.video_sha256,annotation_method:'Source-robot body anchors manually reviewed at keyframes, interpolated in MediaPipe 33-point layout. Background-human candidates are excluded.',annotation_coordinates:'Image-space left/right matches this demo geometric mapper; monocular depth is not calibrated. Wheel ankle anchors are wheel centres; wheel speed and contact are not inferred.',model_result:'Not an automatically detected or validated robot trajectory; manual geometric reference only.',label:'人工校正骨架 · MediaPipe 33 點格式 · 非自動偵測／非 PPO'}};
 const referencePath=path.join(root,'assets/video-examples',item.robot_type,item.id+'-reference.json');await writeFile(referencePath,JSON.stringify(reference)+'\n');
 item.motion_reference_url='/assets/video-examples/'+item.robot_type+'/'+item.id+'-reference.json';item.reference_segment={...reference.segment};item.skeleton_source=reference.source;item.manual_correction=true;
 references.push({item,reference});
}
await writeFile(path.join(root,'assets/video-examples/retarget-keyframes.json'),JSON.stringify({schema:1,coordinate_width:480,coordinate_height:270,body_indices:POINTS,source:'manual_corrected_mediapipe_reference',annotations},null,2)+'\n');
await writeFile(catalogPath,JSON.stringify(catalog,null,2)+'\n');
console.log('Wrote five source-aligned manual reference JSON files.');
if(process.argv.includes('--references-only'))process.exit(0);
const require=createRequire(import.meta.url),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const html=`<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><style>body{margin:0;background:#f5f6f8;color:#253140;font-family:Arial,"Noto Sans CJK TC",sans-serif}header{height:66px;padding:0 26px;display:flex;justify-content:space-between;align-items:center;background:#fff;border-bottom:1px solid #dfe4ed}h1{font-size:18px;margin:0}header span{font-size:11px;color:#68768a}.panels{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:20px 24px 12px}.panel{border:1px solid #dbe1eb;border-radius:12px;overflow:hidden;background:white}.label{height:43px;display:flex;align-items:center;justify-content:space-between;padding:0 16px;font-size:11px;letter-spacing:1px}.label b{font-size:10px;font-weight:400;color:#719088}.source{height:270px;position:relative;background:#111}img.source-image,canvas.overlay{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}#model{height:270px}.foot{box-sizing:border-box;padding:8px 16px;font-size:9px;color:#6d7b8b;height:36px;line-height:1.5}footer{display:flex;justify-content:space-between;padding:6px 26px 10px;color:#6b798c;font-size:10px}.chip{color:#007960!important;background:#e3f6ef;border-radius:6px;padding:5px 8px}strong{font-weight:500}</style><header><h1 id="title"></h1><span>VIDEO → LANDMARKS → NATIVE Q1</span></header><div class="panels"><div class="panel"><div class="label"><span>SOURCE / 原片＋骨架</span><b class="chip">人工校正 · MediaPipe 33 點格式</b></div><div class="source"><img id="video" class="source-image" alt="來源機器人影片影格"><canvas class="overlay" id="overlay" width="480" height="270"></canvas></div><div class="foot">來源機器人關鍵影格人工校正；未把背景人物當成來源。</div></div><div class="panel"><div class="label"><span id="robot-title"></span><b class="chip">同步幾何映射</b></div><div id="model"></div><div class="foot">原生 URDF 限位＋mimic；不代表動力學、輪速或新 PPO 已驗證。</div></div></div><footer><span id="time"></span><strong>單鏡頭人工參考 · 非自動偵測 · 非物理模擬</strong></footer><script type="module">import {createRobotViewer} from '/public/robot-viewer.js';window.requestAnimationFrame=()=>0;window.cancelAnimationFrame=()=>{};const viewer=await createRobotViewer(document.querySelector('#model'));window.viewer=viewer;window.ready=true;window.setExample=async function(item,robot,reference){document.querySelector('#title').textContent=item.title;document.querySelector('#robot-title').textContent=robot.id==='leg'?'RETARGET / Q1 LEG':'RETARGET / Q1 WHEEL';const video=document.querySelector('#video');video.src='/__frames/'+item.id+'/0000.png';await video.decode();await viewer.loadRobot(robot);viewer.setPlaying(false);window.ref=reference;window.item=item;};const edges=[[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24],[23,25],[25,27],[24,26],[26,28],[27,31],[28,32]];window.renderFrame=async function(index){const f=window.ref.frames[index],video=document.querySelector('#video');video.src='/__frames/'+window.item.id+'/'+String(Math.round(f.source_time*10)).padStart(4,'0')+'.png';await video.decode();const canvas=document.querySelector('#overlay'),ctx=canvas.getContext('2d');ctx.clearRect(0,0,480,270);ctx.strokeStyle='#00ecad';ctx.fillStyle='#00ecad';ctx.lineWidth=2.4;for(const [a,b]of edges){const p=f.landmarks[a],q=f.landmarks[b];ctx.beginPath();ctx.moveTo(p.x*480,p.y*270);ctx.lineTo(q.x*480,q.y*270);ctx.stroke();}for(const i of[0,11,12,13,14,15,16,23,24,25,26,27,28]){const p=f.landmarks[i];ctx.beginPath();ctx.arc(p.x*480,p.y*270,i===0?5:3,0,7);ctx.fill();}viewer.setJoints(f.joints);viewer.capture();document.querySelector('#time').textContent='原影片 '+(window.item.start_s+f.source_time).toFixed(2)+' s · 片段 '+f.source_time.toFixed(2)+' s';};</script></html>`;
const types={'.js':'text/javascript','.json':'application/json','.mp4':'video/mp4','.stl':'application/octet-stream','.jpg':'image/jpeg','.gif':'image/gif'};
const server=createServer(async(req,res)=>{try{const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);if(pathname==='/'){res.writeHead(200,{'Content-Type':'text/html;charset=utf-8'});res.end(html);return;}if(pathname.startsWith('/__frames/')){const match=pathname.match(/^\/__frames\/([a-z0-9-]+)\/(\d{4}\.png)$/);if(!match||!annotations[match[1]])throw Error('frame path');const data=await readFile(path.join('/tmp/q1-retarget-source-'+match[1],match[2]));res.writeHead(200,{'Content-Type':'image/png'});res.end(data);return;}if(!pathname.startsWith('/public/')&&!pathname.startsWith('/assets/')){res.writeHead(404);res.end();return;}const file=path.resolve(root,'.'+pathname);if(!file.startsWith(root+path.sep))throw Error('path');const data=await readFile(file);res.writeHead(200,{'Content-Type':types[path.extname(file)]||'application/octet-stream'});res.end(data);}catch{res.writeHead(404);res.end();}});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try{const page=await browser.newPage({viewport:{width:1024,height:480},deviceScaleFactor:1}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:'+port+'/');await page.waitForFunction(()=>window.ready);
 for(const {item,reference}of references.filter(v=>!process.env.Q1_REFERENCE_ONLY||process.env.Q1_REFERENCE_ONLY.split(',').includes(v.item.id))){
  const temporary='/tmp/q1-retarget-'+item.id;await rm(temporary,{recursive:true,force:true});await mkdir(temporary,{recursive:true});const sourceFrames='/tmp/q1-retarget-source-'+item.id;await mkdir(sourceFrames,{recursive:true});execFileSync(process.env.Q1_FFMPEG||'ffmpeg',['-y','-i',path.join(root,item.video_url),'-vf','fps='+FPS+':start_time=0','-start_number','0',path.join(sourceFrames,'%04d.png'),'-loglevel','error']);const robot=robots.find(r=>r.id===item.robot_type);
  await page.evaluate(async({item,robot,reference})=>window.setExample(item,robot,reference),{item,robot,reference});await page.waitForFunction(()=>document.querySelector('#model').dataset.modelReady==='true');
  for(let i=0;i<reference.frames.length;i++){await page.evaluate(index=>window.renderFrame(index),i);await page.screenshot({path:path.join(temporary,String(i).padStart(4,'0')+'.png')});}
  const output=path.join(root,'assets/video-examples',item.robot_type,item.id+'-retarget.gif');execFileSync(process.env.Q1_FFMPEG||'ffmpeg',['-y','-framerate',String(FPS),'-i',path.join(temporary,'%04d.png'),'-frames:v',String(reference.frames.length),'-vf','split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle','-loop','0',output,'-loglevel','error']);
  item.retarget_gif_url='/assets/video-examples/'+item.robot_type+'/'+item.id+'-retarget.gif';await writeFile(catalogPath,JSON.stringify(catalog,null,2)+'\n');console.log('Rendered '+item.id+': '+reference.frames.length+' frames');
 }
 if(errors.length)throw Error(errors.join('\n'));
}finally{await browser.close();await new Promise(resolve=>server.close(resolve));}
