import assert from 'node:assert/strict';
import {validateSegment} from '../public/clip-editor.js';
assert.deepEqual(validateSegment(.2,2.8,3),{start_s:.2,end_s:2.8});
for(const args of [[-1,2,3],[2,1,3],[0,.49,3],[0,61,100],[NaN,2,3],[0,Infinity,3],[0,4,3]])assert.throws(()=>validateSegment(...args));
console.log('Clip bounds passed: finite, positive, minimum and maximum length, duration and millisecond precision.');
