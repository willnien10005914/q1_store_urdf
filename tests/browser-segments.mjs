/** Existing server: PLAYWRIGHT_MODULE=/path/to/playwright Q1_STORE_URL=http://127.0.0.1:8876 node tests/browser-segments.mjs */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.Q1_STORE_URL||'http://127.0.0.1:8876';
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base);
 await page.locator('#studio-viewer[data-model-ready="true"]').waitFor({timeout:60000});
 await page.waitForFunction(()=>document.querySelector('#source-video').readyState>=1&&document.querySelector('#source-video').src.includes('h2x-guard.mp4'),{},{timeout:30000});
 assert.equal(await page.locator('[data-video-example]').count(),3);
 assert.equal(await page.locator('[data-video-example="h2x-guard"]').getAttribute('class').then(v=>v.includes('active')),true);
 assert.ok((await page.locator('#pose-badge').innerText()).includes('ROBOT REFERENCE'));
 await page.locator('#clip-start').fill('0.25');await page.locator('#clip-end').fill('2.5');await page.locator('#clip-action-name').fill('H2-X Guard segment QA');await page.locator('#save-action').click();
 assert.equal(await page.locator('#draft-list .action-draft').count(),1);
 assert.equal(await page.locator('#clip-original-time').innerText(),'原影片 33.25–35.50 s');
 await page.locator('[data-video-example="h2x-punch"]').click();await page.waitForFunction(()=>document.querySelector('#source-video').readyState>=1&&document.querySelector('#source-video').src.includes('h2x-punch.mp4'));
 await page.locator('#clip-start').fill('0.5');await page.locator('#clip-end').fill('2.75');await page.locator('#clip-action-name').fill('H2-X Punch segment QA');await page.locator('#save-action').click();
 assert.equal(await page.locator('#draft-list .action-draft').count(),2);
 const drafts=await page.evaluate(()=>JSON.parse(localStorage.getItem('q1-action-drafts-v1')));assert.deepEqual(drafts.map(d=>d.source.id),['h2x-guard','h2x-punch']);
 await page.locator('#draft-list .action-draft').first().getByRole('button',{name:'繼續設定 →'}).click();
 await page.locator('[data-next="world"]').click();await page.locator('[name=run-mode][value=worker]').check();
 let responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/jobs')&&r.request().method()==='POST');await page.locator('#start-job').click();let response=await responsePromise;assert.equal(response.status(),201);const leg=await response.json();
 assert.equal(leg.robot_type,'leg');assert.equal(leg.action_name,'H2-X Guard segment QA');assert.equal(leg.source.id,'h2x-guard');assert.equal(leg.segment.source_start_s,33.25);assert.equal(leg.segment.source_end_s,35.5);assert.equal(leg.state,'queued_for_worker');assert.equal(leg.plan.motion_extraction_ready,false);assert.equal(leg.result,null);assert.equal(leg.plan.robot.control_joints.length,23);assert.equal(leg.plan.robot.urdf_sha256.length,64);assert.equal(leg.motion_id,undefined);
 await page.locator('[data-panel="source"]').click();await page.locator('[data-robot="wheel"]').click();
 await page.waitForFunction(()=>{const v=document.querySelector('#studio-viewer');return v.dataset.modelReady==='true'&&v.dataset.robotType==='wheel'});
 await page.waitForFunction(()=>document.querySelector('#source-video').readyState>=1&&document.querySelector('#source-video').src.includes('x2-slope.mp4'));
 assert.equal(await page.locator('[data-video-example]').count(),2);
 await page.locator('#clip-start').fill('0');await page.locator('#clip-end').fill('0.2');await page.locator('#save-action').click();assert.equal(await page.locator('#draft-list .action-draft').count(),2);assert.match(await page.locator('#toast').innerText(),/0.5/);
 await page.locator('#clip-start').fill('0.2');await page.locator('#clip-end').fill('3.4');await page.locator('#clip-action-name').fill('X2 Slope segment QA');await page.locator('#save-action').click();
 await page.locator('[data-next="training"]:visible').click();await page.locator('[data-next="world"]').click();await page.locator('[name=run-mode][value=worker]').check();
 responsePromise=page.waitForResponse(r=>r.url().endsWith('/api/jobs')&&r.request().method()==='POST');await page.locator('#start-job').click();response=await responsePromise;assert.equal(response.status(),201);const wheel=await response.json();assert.equal(wheel.robot_type,'wheel');assert.equal(wheel.source.id,'x2-slope');assert.equal(wheel.segment.source_start_s,24.2);assert.equal(wheel.segment.source_end_s,27.4);assert.equal(wheel.action_name,'X2 Slope segment QA');assert.equal(wheel.plan.motion_extraction_ready,false);assert.equal(wheel.plan.task_registered,false);assert.equal(wheel.result,null);
 await page.setViewportSize({width:390,height:844});await page.locator('[data-panel="source"]').click();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.reload();await page.locator('#draft-list .action-draft').first().waitFor();assert.equal(await page.locator('#draft-list .action-draft').count(),3);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({passed:true,leg_job:leg.id,wheel_job:wheel.id,drafts:3,source_offsets:[leg.segment.source_start_s,wheel.segment.source_start_s],page_errors:errors,mobile_width:390}));
}finally{await browser.close();}
