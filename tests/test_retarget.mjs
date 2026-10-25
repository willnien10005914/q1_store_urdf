import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source=await readFile(new URL('../public/retarget.js',import.meta.url),'utf8');
const {mapLandmarks,resolveNativeMimics,createRetargetController,normalizeVideoSegment}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
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

assert.deepEqual(normalizeVideoSegment({start_s:1.5,end_s:3.5},10),{start_s:1.5,end_s:3.5});
for(const [segment,duration]of [[{start_s:-1,end_s:2},10],[{start_s:2,end_s:2.4},10],[{start_s:0,end_s:61},70],[{start_s:0,end_s:11},10],[{start_s:NaN,end_s:4},10],[{start_s:'1',end_s:2},10],[{start_s:0,end_s:2},Infinity]])assert.throws(()=>normalizeVideoSegment(segment,duration),'reject invalid or unknown playable-video ranges');

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
  controller.stop();
  const statuses=[],realVideo={videoWidth:640,videoHeight:360,currentTime:0,duration:10,readyState:2,paused:true,loop:true,
    pause(){this.paused=true;},async play(){this.paused=false;}};
  let detections=0;
  const real=createRetargetController({video:realVideo,overlay,poseDetector:{detectForVideo(){detections++;return {landmarks:[pose],worldLandmarks:[]};},close(){}},onStatus:message=>statuses.push(message)});
  try{
    await assert.rejects(()=>real.start({segment:{start_s:2,end_s:11}}));
    await real.start({robot:robots[0],segment:{start_s:2,end_s:3}});
    assert.equal(realVideo.currentTime,2,'capture seeks to the chosen local start');
    assert.equal(realVideo.loop,false,'whole-video looping cannot skip completion at a segment ending at duration');
    step(100);realVideo.currentTime=2.2;step(100);realVideo.currentTime=2.8;step(100);
    assert.equal(detections,3);
    realVideo.currentTime=3.05;step(100);
    assert.equal(realVideo.paused,true,'segment completion pauses playback');
    assert.equal(realVideo.loop,true,'the original playback loop setting is restored');
    assert.equal(detections,3,'no frame at or past the selected end enters the detector');
    realVideo.currentTime=3.2;step(100);assert.equal(real.getFrames().length,3,'completion keeps the captured reference and stops inference');
    const segmentMotion=await real.exportMotion();
    assert.deepEqual(segmentMotion.segment,{start_s:2,end_s:3});
    assert.equal(segmentMotion.demo,false);assert.equal(segmentMotion.source,'mediapipe_pose_landmarker');
    assert(segmentMotion.frames.every(f=>f.source_time>=2&&f.source_time<3));
    assert(segmentMotion.frames.every((f,i,a)=>i===0||f.time>a[i-1].time));
    assert(statuses.at(-1).startsWith('片段辨識完成'));
    real.resetCapture();assert.equal((await real.exportMotion()).segment,undefined,'source replacement discards previous segment metadata');
    await real.start({robot:robots[0],segment:{start_s:2,end_s:3},requireTargetSelection:true});step(100);
    assert.equal(realVideo.paused,true,'robot videos pause on their first candidate for manual review');
    assert(statuses.at(-1).startsWith('影片已暫停'));
    const candidateDetections=detections;step(3000);assert.equal(detections,candidateDetections,'candidate review keeps the displayed frame without inference or capture');
    assert.equal(real.getFrames().length,0,'robot videos must not automatically capture a background person');assert.equal((await real.exportMotion()).target_person,null);
    real.selectPerson('person-0');assert.equal(realVideo.paused,false,'explicit candidate selection resumes the paused segment');
    realVideo.currentTime=2.4;step(100);realVideo.currentTime=2.6;step(100);assert.equal(real.getFrames().length,2,'only an explicitly selected robot-video target can produce a reference');
    assert(real.getFrames().every(frame=>frame.person==='person-0'),'review time does not expire the selected candidate identity');
    const reviewed=await real.exportMotion();assert.equal(reviewed.demo,false);assert.equal(reviewed.source,'mediapipe_pose_landmarker');
    assert(reviewed.frames.every(frame=>frame.source_time>=2&&frame.source_time<3));
    real.resetCapture();const resetDetections=detections;real.selectPerson('person-0');step(100);assert.equal(detections,resetDetections,'selecting a stale candidate after reset cannot resume inference');
  }finally{real.dispose();}

}finally{
  controller.dispose();Object.defineProperty(globalThis,'performance',{configurable:true,value:originalPerformance});
  delete globalThis.devicePixelRatio;delete globalThis.requestAnimationFrame;delete globalThis.cancelAnimationFrame;
}
console.log('Retarget fixtures passed: mirrored axes, native limits, recursive mimic coupling, target isolation, source reset, segment boundaries/completion, monotonic capture and export consistency.');
