/**
 * Record loaded-model retargeting and registered-robot deployment operations.
 * Startup and the real 30-second wait are omitted from the frame sequence;
 * application clocks, jobs and results are never modified.
 *
 * PLAYWRIGHT_MODULE=/path/to/playwright Q1_STORE_URL=http://127.0.0.1:8876 \
 * Q1_RECORD_ROBOT=leg node scripts/record_deploy_demo.mjs
 */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
const require=createRequire(import.meta.url),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url))),type=process.env.Q1_RECORD_ROBOT||'leg';
if(!['leg','wheel'].includes(type))throw Error('Q1_RECORD_ROBOT must be leg or wheel.');
const base=(process.env.Q1_STORE_URL||'http://127.0.0.1:8876').replace(/\/$/,''),clipId=type==='leg'?'h2x-guard':'x2-slope';
const out=path.join(process.env.Q1_RECORD_DIR||'/tmp/q1-deploy-demo',type+'-'+Date.now()),framesDir=path.join(out,'frames');
await mkdir(framesDir,{recursive:true});await mkdir(path.join(root,'docs/images'),{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1280,height:1000},deviceScaleFactor:1,acceptDownloads:true});
const page=await context.newPage(),errors=[];let frameNumber=0;const stages=[];
page.on('pageerror',error=>errors.push(error.message));
const pause=ms=>page.waitForTimeout(ms);
const align=async selector=>page.locator(selector).evaluate(e=>scrollTo({top:e.getBoundingClientRect().top+scrollY-100,behavior:'instant'}));
async function frame(stage,repeat=1){
 const filename=path.join(framesDir,String(frameNumber++).padStart(5,'0')+'.png');
 await page.screenshot({path:filename});stages.push({frame:frameNumber-1,stage});
 if(repeat>1){const bytes=await readFile(filename);for(let i=1;i<repeat;i++){await writeFile(path.join(framesDir,String(frameNumber++).padStart(5,'0')+'.png'),bytes);stages.push({frame:frameNumber-1,stage});}}
}
async function capture(stage,seconds=2,fps=6){const end=Date.now()+seconds*1000;do{const start=Date.now();await frame(stage);await pause(Math.max(0,1000/fps-(Date.now()-start)));}while(Date.now()<end);}
async function ffmpeg(args){await new Promise((resolve,reject)=>{const command=spawn(process.env.Q1_FFMPEG||'ffmpeg',args,{stdio:['ignore','ignore','pipe']});let stderr='';command.stderr.on('data',data=>{stderr+=data.toString();});command.on('error',reject);command.on('close',code=>code===0?resolve():reject(Error(stderr)));});}
let job,registration,deployment,downloadPath;const sampledReferenceTimes=[];
async function seekReference(time){
 const state=await page.evaluate(async value=>{const app=await import('/app.js');return app.seekReferenceFrame(value);},time);
 assert.equal(state.model_ready,true,'source-time sampling requires a fully loaded Q1');
 await pause(50);
}
async function captureReferenceFrames(count=18){const range=await page.evaluate(()=>({start:Number(document.querySelector('#clip-start').value),end:Number(document.querySelector('#clip-end').value)}));for(let i=0;i<count;i++){const time=range.start+.04+(Math.max(.1,range.end-range.start-.12))*i/(count-1);await seekReference(time);sampledReferenceTimes.push(Number(time.toFixed(4)));await frame('animated-retargeting-source-time-'+time.toFixed(3));}}
try{
 await page.goto(base+'/');
 await page.waitForFunction(()=>document.querySelectorAll('[data-model-ready="true"]').length===2,{},{timeout:60000});
 await page.evaluate(()=>document.fonts.ready);
 if(type==='wheel'){
  await page.locator('[data-robot="wheel"]').click();
  await page.waitForFunction(()=>{const e=document.querySelector('#studio-viewer');return e.dataset.modelReady==='true'&&e.dataset.robotType==='wheel';},{},{timeout:60000});
 }
 await page.locator('[data-video-example="'+clipId+'"]').click();
 await page.waitForFunction(()=>document.querySelector('#source-video').readyState>=2,{},{timeout:30000});
 await page.locator('#play-reference').waitFor({state:'visible',timeout:60000});
 await page.waitForFunction(()=>!document.querySelector('#play-reference').disabled,{},{timeout:60000});
 await align('.dual-preview');
 await page.locator('#play-reference').click();
 await align('.dual-preview');await pause(200);await seekReference(.04);
 assert.equal(await page.locator('.loading-model').count(),0,'recording cannot start with a loading model');
 assert.equal(await page.locator('#studio-viewer').getAttribute('data-model-ready'),'true');
 const firstModel={robot_type:type,meshes:+await page.locator('#studio-viewer').getAttribute('data-mesh-count')};
 assert.equal(firstModel.meshes,type==='leg'?30:28,'first frame must include all native meshes');
 await frame('loaded-source-skeleton-and-q1',4);
 await captureReferenceFrames(18);
 await align('#clip-editor');
 await page.locator('#clip-action-name').fill(type==='leg'?'Q1 Leg · 護架跨步骨架參考':'Q1 Wheel · 斜坡骨架參考');
 await frame('name-video-segment',5);
 await page.locator('#save-action').click();await pause(250);await frame('save-named-segment',4);
 await page.locator('[data-next="training"]:visible').click();await align('#panel-training');
 await page.selectOption('#rl-library','rsl_rl');await page.selectOption('#physics-engine','newton_mjwarp');await frame('training-design',7);
 await page.locator('[data-next="world"]').click();await align('#panel-world');
 await page.locator('[data-landmark="'+(type==='leg'?'eiffel':'taipei101')+'"]').click();await frame('choose-rendering-plan',6);
 const created=page.waitForResponse(r=>r.url().endsWith('/api/jobs')&&r.request().method()==='POST').then(async r=>({status:r.status(),body:await r.json()}));
 await page.locator('#start-job').click();const response=await created;assert.equal(response.status,201,JSON.stringify(response.body));job=response.body;
 await align('#panel-result');await frame('actual-demo-started',5);
 // Wait for actual service completion without generating thirty seconds of static frames.
 await page.waitForFunction(()=>document.querySelector('#share-job').disabled===false,{},{timeout:45000});
 await page.locator('#result-video').evaluate(video=>video.play());await pause(300);await capture('actual-completed-demo-historical-reference',1.7);
 await page.locator('#deploy-job').click();await pause(250);await frame('deploy-registered-q1',6);
 const serial='DEMO-'+type.toUpperCase()+'-'+Date.now();
 await page.locator('#robot-name').fill(type==='leg'?'我的 Q1 Leg':'我的 Q1 Wheel');await page.locator('#robot-serial').fill(serial);
 await page.selectOption('#registered-robot-type',type);
 await frame('register-target-q1',5);
 const registered=page.waitForResponse(r=>r.url().endsWith('/api/robots')&&r.request().method()==='POST').then(async r=>({status:r.status(),body:await r.json()}));
 await page.locator('#register-robot').evaluate(form=>form.requestSubmit());const registeredResponse=await registered;assert.equal(registeredResponse.status,201,JSON.stringify(registeredResponse.body));registration=registeredResponse.body;
 await page.selectOption('#deployment-robot',registration.id);await pause(200);await frame('registered-target-selected',6);
 const deployed=page.waitForResponse(r=>r.url().endsWith('/api/deployments')&&r.request().method()==='POST').then(async r=>({status:r.status(),body:await r.json()}));
 const downloaded=page.waitForEvent('download',{timeout:20000});
 await page.locator('#download-deployment').click();const deployedResponse=await deployed;assert.equal(deployedResponse.status,201,JSON.stringify(deployedResponse.body));deployment=deployedResponse.body;
 const download=await downloaded;downloadPath=path.join(out,download.suggestedFilename());await download.saveAs(downloadPath);
 const packageBytes=await readFile(downloadPath);assert.equal(packageBytes.subarray(0,4).toString('hex'),'504b0304','download must be a real ZIP archive');
 assert.equal(deployment.target_robot.id,registration.id);assert.equal(deployment.package_kind,'preview_only');assert.equal(deployment.deployable,false);assert.equal(deployment.checkpoint_included,false);assert.equal(deployment.device_acknowledged,false);
 await pause(350);await frame('downloaded-preview-only-package',10);
 await page.setViewportSize({width:390,height:844});await page.locator('#close-deploy').click();await page.locator('#robots').scrollIntoViewIfNeeded();await pause(150);const mobileWidth=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));assert.ok(mobileWidth.document<=mobileWidth.viewport,'registered fleet cannot overflow the mobile viewport');
 assert.deepEqual(errors,[]);
 await context.close();await browser.close();
 const gif=path.join(root,'docs/images/'+type+'-operation-retarget-deploy.gif');
 await ffmpeg(['-y','-threads','2','-framerate','6','-i',path.join(framesDir,'%05d.png'),'-filter_complex','fps=6,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle','-loop','0',gif,'-loglevel','warning']);
 const report={robot_type:type,gif,frames:frameNumber,sampled_reference_times:sampledReferenceTimes,first_frame:firstModel,mobile_width:mobileWidth,errors,job_id:job.id,robot_id:registration.id,deployment_id:deployment.id,package_kind:deployment.package_kind,deployable:deployment.deployable,checkpoint_included:deployment.checkpoint_included,device_acknowledged:deployment.device_acknowledged,download:downloadPath,stages};
 await writeFile(path.join(out,'recording.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({...report,stages:undefined}));
}catch(error){await page.screenshot({path:path.join(out,'failure.png')}).catch(()=>{});await context.close().catch(()=>{});await browser.close().catch(()=>{});console.error('Recording failed; inspect '+out);throw error;}
