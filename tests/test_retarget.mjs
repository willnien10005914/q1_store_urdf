import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../public/retarget.js',import.meta.url),'utf8');
const {mapLandmarks,resolveNativeMimics,createRetargetController}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const {robots}=JSON.parse(await readFile(new URL('../assets/robots/manifest.json',import.meta.url),'utf8'));
// A person with both forearms and thighs bent toward the camera.
const pose=Array.from({length:33},()=>({x:.5,y:.5,z:0,visibility:1}));
for(const [i,x,y,z]of [[11,.4,.2,0],[12,.6,.2,0],[13,.36,.4,-.1],[14,.64,.4,-.1],[15,.26,.32,-.25],[16,.74,.32,-.25],[23,.43,.5,0],[24,.57,.5,0],[25,.4,.7,-.08],[26,.6,.7,-.08],[27,.2,.8,0],[28,.8,.8,0]])Object.assign(pose[i],{x,y,z});

for(const robot of robots){
  const q=mapLandmarks(pose,robot);
  assert(q.left_shoulder_Pitch_joint>0&&q.right_shoulder_Pitch_joint<0,'mirrored shoulder axes must produce opposite coordinates');
  assert(Math.abs(q.left_shoulder_Pitch_joint+q.right_shoulder_Pitch_joint)<1e-12,'symmetric shoulder motion must have equal signed magnitude');
  assert(q.left_elbow_Pitch_joint<0&&q.right_elbow_Pitch_joint>0,'native elbow axes');
  for(const joint of robot.joints){
    if(joint.type==='fixed')continue;
    assert(Number.isFinite(q[joint.name]),`${robot.id}: missing ${joint.name}`);
    if(joint.type!=='continuous')assert(q[joint.name]>=joint.lower-1e-12&&q[joint.name]<=joint.upper+1e-12,`${joint.name} exceeds native limits`);
    if(joint.mimic)assert(Math.abs(q[joint.name]-(q[joint.mimic.joint]*joint.mimic.multiplier+joint.mimic.offset))<1e-12,`${joint.name} violates mechanical coupling`);
  }
  if(robot.id==='wheel')for(const side of ['left','right'])assert(Math.abs(q[`${side}_knee_2_Pitch_joint`]-.444444*q[`${side}_knee_1_Pitch_joint`])<1e-12,'wheel passive knee has the same coordinate sign as its driver');
}
const chain=resolveNativeMimics({driver:10},{id:'fixture',joints:[{name:'driver',type:'revolute',lower:0,upper:2},{name:'follower',type:'revolute',mimic:{joint:'driver',multiplier:.5,offset:1}},{name:'second',type:'revolute',mimic:{joint:'follower',multiplier:-1,offset:.25}}]});
assert.deepEqual(chain,{driver:2,follower:2,second:-1.75},'clamp the driver before recursively deriving passive followers');

// A deterministic animation clock verifies that changing the target cannot mix people.
const originalPerformance=globalThis.performance;
let clock=0,nextId=0,callbacks=new Map();
Object.defineProperty(globalThis,'performance',{configurable:true,value:{now:()=>clock}});
globalThis.devicePixelRatio=1;
globalThis.requestAnimationFrame=callback=>{callbacks.set(++nextId,callback);return nextId;};
globalThis.cancelAnimationFrame=id=>callbacks.delete(id);
const context=new Proxy({},{get:()=>()=>{},set:()=>true});
const overlay={getContext:()=>context,getBoundingClientRect:()=>({width:640,height:360})};
const video={videoWidth:640,videoHeight:360,currentTime:0};
const controller=createRetargetController({video,overlay});
function step(milliseconds){clock+=milliseconds;const queued=[...callbacks.values()];callbacks.clear();for(const callback of queued)callback(clock);}
try{
  await controller.start({demo:true,robot:robots.find(r=>r.id==='wheel')});step(100);step(100);step(100);
  assert.equal(controller.getFrames().length,3);
  assert(controller.getFrames().every(f=>f.person==='person-0'));
  step(3000);assert(controller.getFrames().every(f=>f.person==='person-0'),'authored demo identities must survive a long rendering stall');
  controller.selectPerson('person-1');assert.equal(controller.getFrames().length,0,'new target starts a fresh reference capture');
  step(100);step(100);step(100);
  controller.selectPerson('person-1');assert.equal(controller.getFrames().length,3,'reselecting the same target preserves its capture');
  const motion=await controller.exportMotion(robots.find(r=>r.id==='wheel'));
  assert.equal(motion.target_person,'person-1');assert.equal(motion.frames[0].time,0);
  assert(motion.frames.every(f=>f.person==='person-1'));
  assert(motion.frames.every((f,i,a)=>i===0||f.time>a[i-1].time),'capture times increase strictly');
  for(const frame of motion.frames)for(const [i,name]of motion.joint_names.entries())assert.equal(frame.q[i],frame.joints[name]);
  assert(motion.frames.every(f=>Number.isFinite(f.source_time)));
  controller.stop();
  assert.equal(controller.getFrames().length,3,'stopping preserves capture for an intentional export');
  assert.equal((await controller.exportMotion()).demo,true,'stopping preserves the capture provenance');
  controller.resetCapture();
  assert.equal(controller.getFrames().length,0,'replacing a source or robot discards its previous reference');
  step(100);
  assert.equal(controller.getFrames().length,0,'source replacement cancels the previous capture loop');
  const empty=await controller.exportMotion();
  assert.deepEqual(empty.frames,[]);
  assert.equal(empty.target_person,null);
  assert.equal(empty.demo,false,'a new video cannot inherit procedural demo provenance');
  assert.equal(empty.source,'mediapipe_pose_landmarker');
  await controller.start({demo:true,robot:robots.find(r=>r.id==='leg')});step(100);step(100);
  const fresh=await controller.exportMotion();
  assert.equal(fresh.robot_type,'leg');assert.equal(fresh.target_person,'person-0');
  assert.equal(fresh.frames.length,2);assert.equal(fresh.frames[0].time,0);
}finally{
  controller.dispose();Object.defineProperty(globalThis,'performance',{configurable:true,value:originalPerformance});
  delete globalThis.devicePixelRatio;delete globalThis.requestAnimationFrame;delete globalThis.cancelAnimationFrame;
}
console.log('Retarget fixtures passed: mirrored axes, native limits, recursive mimic coupling, target isolation, source reset, monotonic capture and export consistency.');
