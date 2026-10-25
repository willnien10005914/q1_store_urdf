/** Pure static API parity; no URDF canvas or main UI is rendered. */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.Q1_STORE_STATIC_URL||'http://127.0.0.1:8892';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/__static_qa_empty__');
 const result=await page.evaluate(async()=>{
  const client=await import('/api-client.js'),catalog=await client.initAPI();
  const config={library:'rsl_rl',physics:'newton_mjwarp',rewards:['motion_tracking','balance'],hours:4,landmark:'eiffel'};
  const leg=await client.api('/api/jobs',{robot_type:'leg',source:{type:'video_example',id:'h2x-guard'},segment:{start_s:.4,end_s:2.6},action_name:'Static H2-X guard',mode:'worker',config,target_person:'reference-unassigned'});
  const wheel=await client.api('/api/jobs',{robot_type:'wheel',source:{type:'video_example',id:'x2-slope'},segment:{start_s:.2,end_s:3.4},action_name:'Static X2 slope',mode:'worker',config,target_person:'reference-unassigned'});
  const demoMotion=await client.api('/api/motions',{robot_type:'leg',source:'procedural_kungfu_demo',demo:true,physical_simulation:false,joint_names:['left_hip_Pitch_joint'],frames:[{time:0,q:[0]},{time:.1,q:[0]}]});
  const procedural=await client.api('/api/jobs',{robot_type:'leg',source:{type:'example',id:'kungfu-demo'},action_name:'Procedural reference',motion_id:demoMotion.id,mode:'worker',config});
  let invalid=false,mismatch=false;
  try{await client.api('/api/jobs',{robot_type:'leg',source:{type:'video_example',id:'h2x-guard'},segment:{start_s:0,end_s:9},mode:'worker',config});}catch{invalid=true;}
  try{await client.api('/api/jobs',{robot_type:'wheel',source:{type:'video_example',id:'h2x-guard'},mode:'worker',config});}catch{mismatch=true;}
  const plan=await client.api('/api/jobs/'+leg.id+'/plan');
  return {browser_local:catalog.browser_local,video_examples:catalog.video_examples.length,leg,wheel,procedural,invalid,mismatch,plan,canvas_count:document.querySelectorAll('canvas').length};
 });
 assert.equal(result.browser_local,true);assert.equal(result.video_examples,5);assert.equal(result.canvas_count,0);
 for(const [job,start,end,name] of [[result.leg,33.4,35.6,'Static H2-X guard'],[result.wheel,24.2,27.4,'Static X2 slope']]){
  assert.ok(Math.abs(job.segment.source_start_s-start)<1e-8);assert.ok(Math.abs(job.segment.source_end_s-end)<1e-8);assert.equal(job.action_name,name);assert.equal(job.state,'queued_for_worker');assert.equal(job.result,null);assert.equal(job.plan.motion_extraction_ready,false);assert.equal(job.motion_id,undefined);assert.equal(job.plan.robot.control_joints.length,23);assert.equal(job.plan.newly_trained,false);
 }
 assert.equal(result.plan.action_name,'Static H2-X guard');assert.equal(result.procedural.plan.motion_extraction_ready,false);assert.equal(result.procedural.plan.motion.demo,true);assert.equal(result.procedural.plan.motion.requires_export,true);assert.equal(result.invalid,true);assert.equal(result.mismatch,true);
 await page.reload();const persisted=await page.evaluate(async id=>{const client=await import('/api-client.js');await client.initAPI();return client.api('/api/jobs/'+id);},result.leg.id);assert.equal(persisted.action_name,'Static H2-X guard');assert.equal(persisted.state,'queued_for_worker');
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,rendered_canvases:0,source_offsets:[result.leg.segment.source_start_s,result.wheel.segment.source_start_s],procedural_extraction_ready:result.procedural.plan.motion_extraction_ready,persisted:true,page_errors:errors}));
}finally{await browser.close();}
