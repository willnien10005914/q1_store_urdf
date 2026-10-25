/** Record the actual H2-X and X2-N video-to-Q1 segment workflow; requires a running server. */
import {createRequire} from 'node:module';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url))),type=process.env.Q1_RECORD_ROBOT||'leg',out=process.env.Q1_RECORD_DIR||'/tmp/q1-video-example-recordings';
await mkdir(out,{recursive:true});await mkdir(path.join(root,'docs/images'),{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
const context=await browser.newContext({viewport:{width:1280,height:1000},recordVideo:{dir:out,size:{width:1280,height:1000}}});
const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(process.env.Q1_STORE_URL||'http://127.0.0.1:8876/');
await page.waitForFunction(()=>document.querySelectorAll('[data-model-ready="true"]').length===2,{},{timeout:60000});
if(type==='wheel'){await page.locator('[data-robot="wheel"]').click();await page.waitForFunction(()=>document.querySelector('#studio-viewer').dataset.robotType==='wheel'&&document.querySelector('#studio-viewer').dataset.modelReady==='true');}
const ids=type==='leg'?['h2x-guard','h2x-punch','h2x-turn-kick']:['x2-slope','x2-turn'];
const align=async id=>page.locator('#'+id).evaluate(e=>scrollTo({top:e.getBoundingClientRect().top+scrollY-90,behavior:'instant'}));
await align('video-example-cards');await page.waitForTimeout(1700);
for(const [i,id]of ids.entries()){
 await page.locator('[data-video-example="'+id+'"]').click();await page.waitForFunction(()=>document.querySelector('#source-video').readyState>=2);
 await align('clip-editor');await page.locator('#clip-action-name').fill((type==='leg'?'Q1 Leg':'Q1 Wheel')+' · '+(type==='leg'?['護架跨步','側身出拳','轉身踢腿'][i]:['斜坡通行','坡頂轉向'][i]));
 await page.locator('#clip-start').fill('0.20');await page.locator('#clip-start').dispatchEvent('change');
 const end=await page.locator('#source-video').evaluate(v=>Math.min(v.duration-.1,2.7));await page.locator('#clip-end').fill(end.toFixed(2));await page.locator('#clip-end').dispatchEvent('change');
 await page.locator('#clip-play').click();await page.waitForTimeout(1800);await page.locator('#save-action').click();await page.waitForTimeout(900);
 if(i!==ids.length-1)await align('video-example-cards');
}
// Use the first clip for real MediaPipe, where target tracking is more stable.
await page.locator('[data-video-example="'+ids[0]+'"]').click();await page.waitForFunction(()=>document.querySelector('#source-video').readyState>=2);
await page.locator('#clip-start').fill('0.20');await page.locator('#clip-start').dispatchEvent('change');await page.locator('#clip-action-name').fill(type==='leg'?'Q1 Leg · H2-X 護架跨步':'Q1 Wheel · X2-N 斜坡通行');
await align('clip-editor');await page.locator('#track-video').click();await page.waitForFunction(()=>!document.querySelector('#track-video').disabled,{},{timeout:60000});
await page.waitForTimeout(4200);await align('clip-editor');await page.screenshot({path:path.join(root,'docs/images/'+type+'-video-segments.png')});
await page.locator('[data-next="training"]:visible').click();await page.locator('#panel-training').evaluate(e=>scrollTo({top:e.getBoundingClientRect().top+scrollY-90,behavior:'instant'}));
await page.selectOption('#rl-library','rsl_rl');await page.selectOption('#physics-engine','newton_mjwarp');await page.waitForTimeout(1800);
await page.locator('[data-next="world"]').click();await page.locator('#panel-world').evaluate(e=>scrollTo({top:e.getBoundingClientRect().top+scrollY-90,behavior:'instant'}));
await page.locator('[data-landmark="'+(type==='leg'?'eiffel':'taipei101')+'"]').click();await page.waitForTimeout(1600);
const jobResponse=page.waitForResponse(r=>r.url().endsWith('/api/jobs')&&r.request().method()==='POST').then(async r=>({status:r.status(),body:await r.json()}));
await page.locator('#start-job').click();const job=await jobResponse;if(job.status!==201)throw Error(JSON.stringify(job));
await page.locator('#panel-result').evaluate(e=>scrollTo({top:e.getBoundingClientRect().top+scrollY-90,behavior:'instant'}));
await page.waitForFunction(()=>document.querySelector('#share-job').disabled===false,{},{timeout:45000});
await page.locator('#result-video').evaluate(v=>v.play());await page.waitForTimeout(2600);
await page.screenshot({path:path.join(root,'docs/images/'+type+'-video-result.png')});
const plan=await(await page.request.get((process.env.Q1_STORE_URL||'http://127.0.0.1:8876')+'/api/jobs/'+job.body.id+'/plan')).json();
await context.close();const video=await page.video().path();await browser.close();console.log(JSON.stringify({robot_type:type,video,errors,job_id:job.body.id,motion_id:job.body.motion_id,segment:plan.segment,motion_extraction_ready:plan.motion_extraction_ready}));if(errors.length)process.exitCode=1;
