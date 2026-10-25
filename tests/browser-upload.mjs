// Real upload + MediaPipe smoke test. Requires Playwright and an existing person video.
// PLAYWRIGHT_MODULE=/absolute/path/to/playwright/index.mjs node tests/browser-upload.mjs
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.Q1_BASE_URL||'http://127.0.0.1:8891';
const clip=process.env.Q1_TEST_CLIP||'/tmp/ytclip/clip.mp4';
const report={base,clip,pageErrors:[],networkFailures:[]};
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
try{
 const context=await browser.newContext({viewport:{width:1280,height:900},acceptDownloads:true});
 const page=await context.newPage();
 page.on('pageerror',error=>report.pageErrors.push(error.message));
 page.on('requestfailed',request=>report.networkFailures.push({url:request.url(),error:request.failure()?.errorText}));
 await page.goto(base,{waitUntil:'domcontentloaded'});
 await page.waitForFunction(()=>document.querySelector('#people button'),null,{timeout:30000});
 await page.locator('[data-source="upload"]').click();
 const upload=page.waitForResponse(response=>response.url().endsWith('/api/uploads')&&response.request().method()==='POST');
 await page.locator('#video-file').setInputFiles(clip);
 const uploadResponse=await upload;
 report.uploadStatus=uploadResponse.status();
 assert.equal(report.uploadStatus,201);
 await page.waitForFunction(()=>{const v=document.querySelector('#source-video');return !v.hidden&&v.readyState>=2;},null,{timeout:15000});
 report.video=await page.locator('#source-video').evaluate(v=>({src:v.currentSrc,width:v.videoWidth,height:v.videoHeight,duration:v.duration,readyState:v.readyState}));
 await page.locator('#track-video').click();
 await page.waitForFunction(()=>!document.querySelector('#track-video').disabled,null,{timeout:45000});
 report.trackingStatus=await page.locator('#retarget-status').textContent();
 report.feedback=await page.locator('#source-feedback').textContent();
 const detected=await page.waitForFunction(()=>document.querySelectorAll('#people button').length>0,null,{timeout:20000}).then(()=>true,()=>false);
 report.detected=detected;
 if(detected){
  await page.waitForTimeout(2500);
  report.people=await page.locator('#people button').allTextContents();
  const pending=page.waitForEvent('download',{timeout:10000});
  await page.locator('#export-motion').click();
  const download=await pending;
  const filename='/tmp/q1-upload-retarget.json';await download.saveAs(filename);
  const motion=JSON.parse(await fs.readFile(filename,'utf8'));
  report.motion={source:motion.source,demo:motion.demo,frame_count:motion.frames.length,joints:motion.joint_names.length,target_person:motion.target_person,first:motion.frames[0],last_time:motion.frames.at(-1)?.time};
  assert.equal(motion.source,'mediapipe_pose_landmarker');assert.equal(motion.demo,false);assert(motion.frames.length>=2);
  const saved=await page.request.post(base+'/api/motions',{data:motion});report.motionSaveStatus=saved.status();report.motionRecord=await saved.json();assert.equal(saved.status(),201);
  const retrieved=await page.request.get(base+report.motionRecord.url);const restored=await retrieved.json();assert.equal(restored.frames.length,motion.frames.length);assert.deepEqual(restored.mapping,motion.mapping);
  await page.locator('[data-panel="world"]').click();
  await page.locator('[name="run-mode"][value="worker"]').check();
  const pendingJob=page.waitForResponse(response=>response.url().endsWith('/api/jobs')&&response.request().method()==='POST').then(async response=>({status:response.status(),body:await response.json()}));
  await page.locator('#start-job').click();
  const jobResponse=await pendingJob;report.workerJobStatus=jobResponse.status;const job=jobResponse.body;report.workerJob={id:job.id,state:job.state,motion_id:job.motion_id,result:job.result};
  assert.equal(jobResponse.status,201);assert.equal(job.state,'queued_for_worker');assert(job.motion_id);assert.equal(job.result,null);
  await page.waitForFunction(()=>document.querySelector('#job-state').textContent.includes('等待'),null,{timeout:10000});
 }
 await page.locator('[data-panel="source"]').click();
 await page.locator('[data-source="youtube"]').click();
 await page.locator('#youtube-url').fill('https://example.com/watch?v=abcdefghijk');
 await page.locator('#source-youtube button').click();
 report.invalidYoutube=await page.locator('#source-feedback').textContent();assert.match(report.invalidYoutube,/有效/);
 await page.setViewportSize({width:390,height:844});
 await page.waitForTimeout(300);
 report.mobile=await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,overflow:document.documentElement.scrollWidth>innerWidth}));
 assert.equal(report.mobile.overflow,false);
 assert.deepEqual(report.pageErrors,[]);
 console.log(JSON.stringify(report,null,2));
 await fs.writeFile('/tmp/q1-upload-qa-report.json',JSON.stringify(report,null,2));
 if(!detected)process.exitCode=2;
}catch(error){report.error=error.stack;console.log(JSON.stringify(report,null,2));await fs.writeFile('/tmp/q1-upload-qa-report.json',JSON.stringify(report,null,2));throw error;}
finally{await browser.close();}
