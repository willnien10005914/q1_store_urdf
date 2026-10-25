/** Small module fixture: no browser, GPU, device connection, or real clock wait. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const storage=new Map();globalThis.localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
globalThis.fetch=async url=>url==='/api/catalog'?{ok:false}:{ok:true,json:async()=>JSON.parse(await readFile(new URL('..'+url,import.meta.url),'utf8'))};
const {initAPI,api}=await import('../public/api-client.js');await initAPI();
for(const serial of ['.first','_first',':first','-first'])await assert.rejects(()=>api('/api/robots',{name:'Fixture Q1',robot_type:'leg',serial}));
await assert.rejects(()=>api('/api/robots',{name:'Control\nname',robot_type:'leg',serial:'REGISTRY-000'}));
const robot=await api('/api/robots',{name:'Fixture Q1',robot_type:'leg',serial:'REGISTRY-001'});
assert.equal(robot.connection_state,'not_connected');assert.equal(robot.hardware_delivery,false);
const job=await api('/api/jobs',{robot_type:'leg',source:{type:'example',id:'kungfu-demo'},mode:'demo',config:{library:'rsl_rl',physics:'newton_mjwarp',rewards:['balance'],hours:4,landmark:'taipei101'}});
// Advance only this module fixture's saved timestamp; real browser QA waits actual30s.
const fixture=JSON.parse(storage.get('q1-store-v1'));fixture.jobs[job.id].created_at-=31;fixture.robots[0].control_joints=['wrong-contract'];storage.set('q1-store-v1',JSON.stringify(fixture));
await assert.rejects(()=>api('/api/deployments',{job_id:job.id,robot_id:robot.id}),/關節/);
console.log('Static registry parity passed: serial prefix, control characters, disconnected state and joint-contract mismatch.');
