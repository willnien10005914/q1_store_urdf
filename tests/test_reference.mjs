import assert from 'node:assert/strict';
import {sampleReference,sliceReference} from '../public/reference-player.js';
const reference={source:'manual_corrected_mediapipe_reference',joint_names:['joint'],frames:[0,1,2].map(t=>({time:t,source_time:t,q:[t*.5],landmarks:Array.from({length:33},()=>({x:t*.2,y:.4,z:0}))}))};
const sample=sampleReference(reference,.5);assert.equal(sample.q[0],.25);assert.equal(sample.landmarks[0].x,.1);assert.equal(sample.joints.joint,.25);assert.equal(sampleReference(reference,5).q[0],1);
const result=sliceReference(reference,{start_s:.5,end_s:2});assert.equal(result.frames.length,2);assert.equal(result.frames[0].time,0);assert.equal(result.frames[1].source_time,2);assert.equal(result.source,reference.source);assert.equal(result.physical_simulation,false);
console.log('Reference playback interpolation, clip boundaries, timestamps and provenance passed.');
