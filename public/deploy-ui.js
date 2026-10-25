/** Registered target selection and explicit package download. No hardware is actuated here. */
export function createDeployUI({api,downloadDeployment,toast}){
 const $=id=>document.getElementById(id),node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 let job=null,robots=[];
 function status(message,error=false){$('deployment-status').textContent=message;$('deployment-status').classList.toggle('error',error);}
 function render(){
  $('fleet-cards').replaceChildren();$('fleet-empty').hidden=robots.length>0;
  for(const robot of robots){const card=node('article',undefined,'fleet-card');card.append(node('span',robot.robot_type==='leg'?'BIPED / 雙足':'WHEELED / 雙輪','eyebrow'),node('h3',robot.name),node('p',robot.serial),node('small','URDF '+robot.urdf_sha256.slice(0,12)));$('fleet-cards').append(card);}
  $('deployment-robot').replaceChildren(node('option',job?'選擇相容的已註冊 Q1':'先完成一個動作工作'));$('deployment-robot').firstChild.value='';
  const match=robots.filter(r=>job&&r.robot_type===job.robot_type);
  for(const robot of match){const option=node('option',robot.name+' · '+robot.serial);option.value=robot.id;$('deployment-robot').append(option);}
  if(match.length===1)$('deployment-robot').value=match[0].id;
  $('download-deployment').disabled=!job||job.state!=='complete'||!$('deployment-robot').value;
  $('deploy-job').disabled=!job||job.state!=='complete';
  $('deployment-job-name').textContent=job?(job.action_name||'Q1 Motion'):'註冊你的 Q1，讓下一個動作有自己的目的地。';
  if(job)$('registered-robot-type').value=job.robot_type;
  $('deployment-package-note').textContent=job?'此工作為概念 demo。下載的是指定機器人的預覽套件，包含工作設定與關節契約；沒有可執行 PPO 權重，也不會傳送控制命令。':'機器人資料只用於選擇下載套件的目標。目前尚未連接實體機器人的接收 agent。';
 }
 async function refresh(){const result=await api('/api/robots');robots=result.robots||[];render();}
 async function open(){status('');await refresh();$('deploy-dialog').showModal();}
 $('deploy-job').onclick=()=>open().catch(e=>toast(e.message));$('manage-robots').onclick=()=>open().catch(e=>toast(e.message));$('close-deploy').onclick=()=>$('deploy-dialog').close();
 $('deployment-robot').onchange=()=>{$('download-deployment').disabled=!job||job.state!=='complete'||!$('deployment-robot').value;};
 $('register-robot').onsubmit=async event=>{event.preventDefault();const button=$('register-robot').querySelector('[type=submit]');button.disabled=true;try{const robot=await api('/api/robots',{name:$('robot-name').value.trim(),robot_type:$('registered-robot-type').value,serial:$('robot-serial').value.trim()});await refresh();if(job&&robot.robot_type===job.robot_type)$('deployment-robot').value=robot.id;$('deployment-robot').dispatchEvent(new Event('change'));status('已註冊 '+robot.name+'。這是裝置資料登錄，尚未建立硬體連線。');$('robot-name').value='';$('robot-serial').value='';}catch(e){status(e.message,true);}finally{button.disabled=false;}};
 $('download-deployment').onclick=async()=>{if(!job)return;const button=$('download-deployment');button.disabled=true;try{const deployment=await api('/api/deployments',{job_id:job.id,robot_id:$('deployment-robot').value});await downloadDeployment(deployment);status('已下載至本瀏覽器：'+deployment.target_robot.name+' 的預覽套件。裝置尚未回報接收，套件不含可執行權重。');toast('Deploy 套件已下載 · '+deployment.target_robot.name+' · Preview only');$('deployment-package-id').textContent='Package '+deployment.id.slice(0,12)+' · checkpoint: none · device acknowledgment: pending';}catch(e){status(e.message,true);}finally{button.disabled=false;}};
 return {refresh,updateJob(value){job=value;render();}};
}
