import {tr,L} from './i18n.js';
/** A clip is expressed in playable-video time; source offsets remain separate provenance. */
export function validateSegment(start,end,duration){
 if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end-start<.5||end-start>60)throw Error(tr('片段長度需介於 0.5 與 60 秒，起點不可小於 0。','Segment length must be 0.5–60 s and the start cannot be below 0.'));
 if(Number.isFinite(duration)&&end>duration+.03)throw Error(tr('片段終點超過影片長度。','Segment end exceeds the video duration.'));
 return {start_s:Math.round(start*1000)/1000,end_s:Math.round(end*1000)/1000};
}
export function createClipEditor({video,onChange=()=>{},onStatus=()=>{},onPreview=()=>{}}){
 const $=id=>document.getElementById(id);let metadata=null,previewing=false;
 function segment(){return validateSegment(+$('clip-start').value,+$('clip-end').value,video.duration);}
 function update(){try{const s=segment(),offset=metadata?.start_s||0;$('clip-duration').textContent=(s.end_s-s.start_s).toFixed(2)+tr(' 秒',' s');$('clip-original-time').textContent=tr('原影片 ','Source ')+(offset+s.start_s).toFixed(2)+'–'+(offset+s.end_s).toFixed(2)+' s';$('clip-error').textContent='';return s;}catch(e){$('clip-error').textContent=e.message;return null;}}
 function changed(){previewing=false;video.pause();update();onChange();}
 function load(meta={}){metadata=meta;previewing=false;$('clip-editor').hidden=false;$('clip-action-name').value=meta.title||tr('我的影片動作','My video motion');$('clip-start').value='0';$('clip-end').value=Number.isFinite(meta.end_s-meta.start_s)?Math.min(60,meta.end_s-meta.start_s).toFixed(3):'5';$('clip-source-title').textContent=meta.source_title||meta.filename||tr('影片片段','Video segment');$('clip-source-link').hidden=!meta.source_url;$('clip-source-link').href=meta.source_url||'#';update();}
 video.addEventListener('loadedmetadata',()=>{if(!$('clip-editor').hidden&&metadata){const duration=video.duration;if(Number.isFinite(duration)){$('clip-start').max=String(duration);$('clip-end').max=String(duration);$('clip-end').value=Math.min(60,duration,Number($('clip-end').value)||duration).toFixed(3);update();}}});
 video.addEventListener('timeupdate',()=>{if(!previewing)return;const s=update();if(!s)return;if(video.currentTime>=s.end_s-.035){if($('clip-loop').checked){video.currentTime=s.start_s;video.play().catch(()=>{});}else{video.pause();previewing=false;}}});
 $('clip-start').addEventListener('change',changed);$('clip-end').addEventListener('change',changed);
 $('clip-set-start').onclick=()=>{$('clip-start').value=video.currentTime.toFixed(3);changed();};$('clip-set-end').onclick=()=>{$('clip-end').value=video.currentTime.toFixed(3);changed();};
 $('clip-play').onclick=async()=>{try{const s=segment();onPreview();video.currentTime=s.start_s;video.loop=false;previewing=true;await video.play();onStatus(tr('正在預覽選定片段；目前尚未開始姿態辨識。','Previewing the selected segment; pose recognition has not started.'));}catch(e){onStatus(e.message,true);}};
 return {load,clear(){metadata=null;previewing=false;$('clip-editor').hidden=true;},getSegment:segment,getActionName(){return $('clip-action-name').value.trim()||tr('我的影片動作','My video motion');},setSegment(s){$('clip-start').value=s.start_s;$('clip-end').value=s.end_s;update();},setActionName(value){$('clip-action-name').value=value;},stopPreview(){previewing=false;video.pause();},metadata(){return metadata;}};
}
