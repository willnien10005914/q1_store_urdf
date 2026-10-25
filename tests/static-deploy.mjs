/** Static server required; this test loads modules on a blank404 page and renders no WebGL. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
const require=createRequire(import.meta.url),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.Q1_STATIC_URL||'http://127.0.0.1:8892';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try{
 const page=await browser.newPage({acceptDownloads:true}),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/blank-static-deploy.html');
 const setup=await page.evaluate(async()=>{
  window.client=await import('/api-client.js');const catalog=await client.initAPI();
  if(!catalog.browser_local)throw Error('Expectedbrowser-local mode');
  const config={library:'rsl_rl',physics:'newton_mjwarp',rewards:['balance','smoothness'],hours:4,landmark:'taipei101'};
  const leg=await client.api('/api/jobs',{robot_type:'leg',mode:'demo',source:{type:'video_example',id:'h2x-guard'},segment:{start_s:.2,end_s:2.8},action_name:'StaticlegpackageQA',config});
  const wheel=await client.api('/api/jobs',{robot_type:'wheel',mode:'demo',source:{type:'video_example',id:'x2-slope'},segment:{start_s:.2,end_s:3.8},action_name:'StaticwheelpackageQA',config});
  const robot=await client.api('/api/robots',{name:'My QA Q1',robot_type:'leg',serial:'Q1-LEG-STATIC-001'});
  const other=await client.api('/api/robots',{name:'Other QA Q1',robot_type:'wheel',serial:'Q1-WHEEL-STATIC-002'});
  let incomplete=false;try{await client.api('/api/deployments',{job_id:leg.id,robot_id:robot.id});}catch{incomplete=true;}
  let duplicate=false;try{await client.api('/api/robots',{name:'Duplicate',robot_type:'leg',serial:'q1-leg-static-001'});}catch{duplicate=true;}
  window.qa={leg,wheel,robot,other};return {incomplete,duplicate,canvases:document.querySelectorAll('canvas').length};
 });
 assert.deepEqual(setup,{incomplete:true,duplicate:true,canvases:0});
 // Real elapsed demo time: this QA does not alter the clock or the stored job timestamp.
 await page.waitForTimeout(31000);
 const metadata=await page.evaluate(async()=>{
  const {leg,robot,other}=qa;
  let mismatch=false;try{await client.api('/api/deployments',{job_id:leg.id,robot_id:other.id});}catch{mismatch=true;}
  const deployment=await client.api('/api/deployments',{job_id:leg.id,robot_id:robot.id});qa.deployment=deployment;
  return {mismatch,deployment,robots:(await client.api('/api/robots')).robots.length};
 });
 assert.equal(metadata.mismatch,true);assert.equal(metadata.robots,2);
 const deployment=metadata.deployment;
 assert.equal(deployment.package_kind,'preview_only');assert.equal(deployment.target_robot.serial,'Q1-LEG-STATIC-001');
 for(const key of ['deployable','checkpoint_included','hardware_activation_allowed','device_acknowledged'])assert.equal(deployment[key],false,key);
 const pending=page.waitForEvent('download');const info=await page.evaluate(()=>client.downloadDeployment(qa.deployment));const download=await pending;
 const archivePath='/tmp/q1-static-deploy-qa.zip';await download.saveAs(archivePath);assert.ok(info.size>1000);assert.match(download.suggestedFilename(),/preview\.zip$/);
 const archiveReport=JSON.parse(execFileSync('python3',['-c',`import json,zipfile,sys\nwith zipfile.ZipFile(sys.argv[1]) as z:\n m=json.loads(z.read('manifest.json'));p=json.loads(z.read('job-plan.json'));c=json.loads(z.read('robot-contract.json'));assert m['package_kind']=='preview_only';assert m['deployable'] is False;assert m['checkpoint_included'] is False;assert m['hardware_activation_allowed'] is False;assert m['device_acknowledged'] is False;assert not any(n.endswith(('.pt','.onnx','.pth')) for n in z.namelist());assert p['segment']['source_start_s']==33.2;assert c['id']=='leg';print(json.dumps({'files':z.namelist(),'target':m['target_robot']['serial'],'source_start_s':p['segment']['source_start_s'],'deployable':m['deployable']}))`,archivePath],{encoding:'utf8'}));
 await page.reload();const persisted=await page.evaluate(async()=>{const c=await import('/api-client.js');await c.initAPI();return {robots:(await c.api('/api/robots')).robots.length,deployments:(await c.api('/api/deployments')).deployments.length,canvases:document.querySelectorAll('canvas').length};});
 assert.deepEqual(persisted,{robots:2,deployments:1,canvases:0});assert.deepEqual(errors,[]);
 console.log(JSON.stringify({passed:true,archive:archivePath,archiveReport,persisted,page_errors:errors}));
}finally{await browser.close();}
