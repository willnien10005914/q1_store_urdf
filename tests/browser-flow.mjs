/** Run against an existing server: Q1_STORE_URL=http://127.0.0.1:8876 npm run test:browser */
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.Q1_STORE_URL||'http://127.0.0.1:8876';
const browser=await chromium.launch({headless:true,args:['--no-sandbox','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader']});
try{
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await page.locator('#studio-viewer[data-model-ready="true"]').waitFor({timeout:30000});await page.locator('#load-example').click();await page.locator('#people button').first().waitFor({timeout:30000});
 assert.equal(await page.locator('#people button').count(),2);
 await page.locator('[data-robot="wheel"]').click();await page.waitForFunction(()=>{const e=document.querySelector('#studio-viewer');return e.dataset.modelReady==='true'&&e.dataset.robotType==='wheel';});
 await page.locator('[data-robot="leg"]').click();await page.waitForFunction(()=>{const e=document.querySelector('#studio-viewer');return e.dataset.modelReady==='true'&&e.dataset.robotType==='leg';});
 await page.locator('#load-example').click();await page.locator('#people button').last().waitFor();await page.locator('#people button').last().click();await page.waitForFunction(()=>document.querySelector('#people button:last-child')?.getAttribute('aria-pressed')==='true');await page.waitForTimeout(800);
 await page.locator('[data-next="training"]:visible').click();
 await page.selectOption('#rl-library','skrl');await page.selectOption('#physics-engine','newton_mjwarp');
 await page.locator('[data-next="world"]').click();await page.locator('[data-landmark="eiffel"]').click();
 const jobResponse=page.waitForResponse(r=>r.url().endsWith('/api/jobs')&&r.request().method()==='POST');
 await page.locator('#start-job').click();const response=await jobResponse;assert.equal(response.status(),201);const job=await response.json();assert.equal(job.target_person,'person-1');assert.ok(job.motion_id);
 await page.waitForFunction(()=>document.querySelector('#share-job').disabled===false,{},{timeout:45000});
 assert.equal(await page.locator('#job-percent').innerText(),'100%');
 await page.locator('#result-video').evaluate(v=>v.play());await page.waitForTimeout(700);
 assert.ok(await page.locator('#result-video').evaluate(v=>v.currentTime>0&&v.videoWidth>0));
 const final=await (await page.request.get(base+'/api/jobs/'+job.id)).json();assert.equal(final.result.newly_trained,false);assert.equal(final.result.landmark_rendered,false);
 await page.locator('#share-job').click();await page.locator('#listing-title').fill('Kung Fu browser verification');await page.locator('#listing-price').fill('3.50');
 await page.locator('#share-form button').click();await page.locator('#community-cards .button').first().waitFor();
 const receiptResponse=page.waitForResponse(r=>r.url().endsWith('/api/purchases')&&r.request().method()==='POST');
 await page.locator('#community-cards .button').first().click();const receipt=await (await receiptResponse).json();assert.equal(receipt.charged_amount,0);
 await page.setViewportSize({width:390,height:844});await page.goto(base);await page.locator('#studio-viewer[data-model-ready="true"]').waitFor();
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);console.log('Browser flow passed: dual URDF, coloured target, persisted motion, demo, video, share, no-charge receipt, mobile layout.');
}finally{await browser.close();}
