import * as THREE from './vendor/three/three.module.js';
import {OrbitControls} from './vendor/three/examples/jsm/controls/OrbitControls.js';
import {STLLoader} from './vendor/three/examples/jsm/loaders/STLLoader.js';
import URDFLoader from './vendor/urdf-loader/URDFLoader.js';

/** Native URDF geometry preview. This module never executes physics or a policy. */
export async function createRobotViewer(container,{onStatus=()=>{},onMetrics=()=>{}}={}) {
  const scene=new THREE.Scene();
  scene.background=new THREE.Color('#f5f6f8');
  const camera=new THREE.PerspectiveCamera(32,1,.01,100);
  camera.up.set(0,0,1);
  const renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1;
  renderer.domElement.setAttribute('aria-label','原生 URDF 關節幾何預覽，可拖曳旋轉');
  renderer.domElement.style.cssText='width:100%;height:100%;display:block;touch-action:none';
  container.append(renderer.domElement);
  const controls=new OrbitControls(camera,renderer.domElement);
  controls.enableDamping=true;controls.dampingFactor=.08;controls.minDistance=.5;controls.maxDistance=12;
  scene.add(new THREE.HemisphereLight(0xffffff,0x69788c,1.3));
  for(const [pos,power] of [[[3,-4,6],2.5],[[-4,1,3],.8]]){
    const light=new THREE.DirectionalLight(0xffffff,power);light.position.fromArray(pos);scene.add(light);
  }
  const podium=new THREE.Mesh(new THREE.CylinderGeometry(.73,.8,.045,80),new THREE.MeshStandardMaterial({color:0xffffff,roughness:.9}));
  podium.rotation.x=Math.PI/2;podium.position.z=-.025;scene.add(podium);
  const grid=new THREE.GridHelper(12,60,0xe0e4ea,0xe9ecf1);grid.rotation.x=Math.PI/2;grid.position.z=-.05;scene.add(grid);
  const landmarkGroup=new THREE.Group();scene.add(landmarkGroup);
  let robot=null,manifest=null,disposed=false,frame=0,last=0,playing=false,motion='stand',time=0,loadToken=0,visible=false,hasSize=false,renderingPaused=false;
  const bounds=new THREE.Box3(),target=new THREE.Vector3(0,0,.75);
  const material=new THREE.MeshStandardMaterial({color:0xcbd2df,roughness:.8,metalness:.1,transparent:true,opacity:.33});
  function landmarkBox(x,y,z,w,d,h){const mesh=new THREE.Mesh(new THREE.BoxGeometry(w,d,h),material);mesh.position.set(x,y,z+h/2);landmarkGroup.add(mesh);}
  function setLandmark(id='studio'){
    landmarkGroup.children.forEach(o=>o.geometry?.dispose());landmarkGroup.clear();
    if(/taipei|101/i.test(id)){
      for(let i=0;i<8;i++)landmarkBox(-1.6,1.2,.18+i*.23,.42-i*.025,.42-i*.025,.23);
      landmarkBox(-1.6,1.2,2.02,.025,.025,.42);
    }else if(/eiffel|paris/i.test(id)){
      for(const x of [-1,1])for(const y of [-1,1]){
        const leg=new THREE.Mesh(new THREE.CylinderGeometry(.018,.06,1.95,8),material);
        leg.position.set(-1.6+x*.19,1.2+y*.19,.9);leg.rotation.set(Math.PI/2+x*.13,y*.13,0);landmarkGroup.add(leg);
      }
      landmarkBox(-1.6,1.2,.65,.6,.6,.06);landmarkBox(-1.6,1.2,1.4,.3,.3,.05);landmarkBox(-1.6,1.2,1.85,.025,.025,.45);
    }else if(/times-square/i.test(id)){
      for(let i=0;i<5;i++){const x=-2.2+i*.3,h=.65+(i%3)*.4;landmarkBox(x,1.5,0,.25,.3,h);landmarkBox(x,1.32,h*.45,.19,.035,h*.37);}
    }else if(/shibuya/i.test(id)){
      for(let i=0;i<4;i++)landmarkBox(-2.05+i*.35,1.55,0,.28,.3,.55+(i%2)*.45);
      for(let i=0;i<7;i++)landmarkBox(-2.05+i*.12,.85,-.025,.065,.55,.008);
    }else if(/great-wall/i.test(id)){
      for(let i=0;i<12;i++){const x=-2.3+i*.15,y=1.5+Math.sin(i*.5)*.18,z=.05*Math.sin(i*.4);landmarkBox(x,y,z,.17,.2,.35);if(i%4===0)landmarkBox(x,y,z+.35,.25,.27,.2);else landmarkBox(x,y,z+.35,.06,.2,.06);}
    }else if(/colosseum|rome/i.test(id)){
      for(let i=0;i<24;i++){const a=i*Math.PI/12;landmarkBox(-1.7+Math.cos(a)*.5,1.3+Math.sin(a)*.4,0,.07,.07,.65);}
    }else if(/fuji|japan/i.test(id)){
      const mountain=new THREE.Mesh(new THREE.ConeGeometry(1.2,1.45,64),material);mountain.rotation.x=Math.PI/2;mountain.position.set(-1.7,1.6,.7);landmarkGroup.add(mountain);
    }else if(/liberty|new.?york/i.test(id)){
      landmarkBox(-1.6,1.1,0,.38,.38,.45);landmarkBox(-1.6,1.1,.45,.19,.19,.9);landmarkBox(-1.76,1.1,1.2,.04,.04,.6);
    }else if(/giza|pyramid|egypt/i.test(id)){
      const pyramid=new THREE.Mesh(new THREE.ConeGeometry(.9,1.2,4),material);pyramid.rotation.x=Math.PI/2;pyramid.position.set(-1.5,1.5,.6);landmarkGroup.add(pyramid);
    }else if(!/studio|none/i.test(id)){
      for(let i=0;i<5;i++)landmarkBox(-1.8+i*.25,1.5,0,.18,.2,.4+(i%3)*.3);
    }
    return api;
  }
  function fit(){
    if(!robot)return;robot.updateMatrixWorld(true);bounds.setFromObject(robot);
    const size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
    const height=Math.max(.5,size.z);target.set(center.x,center.y,center.z);
    const distance=Math.max(height,size.x/Math.max(.5,camera.aspect))/(2*Math.tan(camera.fov*Math.PI/360)*.77);
    const direction=new THREE.Vector3(1,-1.7,.48).normalize();
    controls.target.copy(target);camera.position.copy(direction.multiplyScalar(distance).add(center));controls.update();
    onMetrics({height_m:size.z,width_m:size.x,depth_m:size.y,joints:Object.values(robot.joints).filter(j=>j.jointType!=='fixed').length});
  }
  function disposeModel(model){const geometries=new Set(),materials=new Set();model?.traverse(o=>{if(o.geometry&&!geometries.has(o.geometry)){o.geometry.dispose();geometries.add(o.geometry);}for(const m of (Array.isArray(o.material)?o.material:[o.material]))if(m&&!materials.has(m)){m.dispose();materials.add(m);}});}
  function resize(){const width=container.clientWidth,height=container.clientHeight;hasSize=width>0&&height>0;if(!hasSize){last=0;return;}renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();}
  const observer=new ResizeObserver(resize);observer.observe(container);resize();
  const visibilityObserver=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;last=0;});visibilityObserver.observe(container);
  function setJoints(values={}){
    if(!robot)return api;
    for(const [name,value]of Object.entries(values)){
      const j=robot.joints[name];if(!j||j.mimicJoint||!Number.isFinite(value))continue;
      const lower=Number.isFinite(j.limit?.lower)?j.limit.lower:-Infinity,upper=Number.isFinite(j.limit?.upper)?j.limit.upper:Infinity;
      robot.setJointValue(name,j.jointType==='continuous'?value:THREE.MathUtils.clamp(value,lower,upper));
    }
    return api;
  }
  function demoPose(t){
    const swing=Math.sin(t*1.7),pulse=(Math.sin(t*2.1)+1)/2,wheel=manifest?.id==='wheel';
    const q={center_waist_Yaw_joint:.22*swing,center_waist_Roll_joint:.025*swing,center_waist_Pitch_joint:.07};
    for(const [side,sign]of [['left',1],['right',-1]]){
      const lead=side==='left'?pulse:1-pulse;
      q[`${side}_shoulder_Pitch_joint`]=sign*(motion==='kungfu'?.45+lead*.9:.12);
      q[`${side}_shoulder_Roll_joint`]=sign*(motion==='kungfu'?.12+.25*lead:.08);
      q[`${side}_elbow_Pitch_joint`]=-sign*(motion==='kungfu'?.35+(1-lead)*1.4:.25);
      q[`${side}_hip_Pitch_joint`]=sign*(motion==='kungfu'?.12+.2*lead:.08);
      q[`${side}_hip_Roll_joint`]=sign*(motion==='kungfu'?.12:.025);
      if(wheel){q[`${side}_knee_1_Pitch_joint`]=-sign*(.35+.1*lead);q[`${side}_wheel_Pitch_joint`]=motion==='kungfu'?t*.3:0;}
      else{q[`${side}_knee_Pitch_joint`]=sign*(motion==='kungfu'?.32+.18*lead:.16);q[`${side}_ankle_Pitch_joint`]=-sign*.08;}
    }
    return q;
  }
  async function loadRobot(entry){
    const token=++loadToken;manifest=entry;container.dataset.modelReady='false';onStatus('正在載入原生 URDF 與 meshes…');
    let loaded;
    const manager=new THREE.LoadingManager();
    const loader=new URDFLoader(manager);loader.parseCollision=false;
    const meshLoads=[];
    let started=0,finished=0;
    const showProgress=()=>{const text=`正在載入 Q1 模型 ${finished}/${Math.max(started,1)}`;document.querySelectorAll('.loading-model').forEach(label=>{label.textContent=text;});onStatus(text);};
    // FileLoader shares pending URLs across viewers without accounting for the
    // second LoadingManager. Wait for each actual mesh attachment instead.
    loader.loadMeshCb=(path,loadingManager,done)=>{
      started++;showProgress();
      meshLoads.push(new Promise((resolve,reject)=>{
        if(!/\.stl$/i.test(path)){const error=new Error('不支援的原生 mesh：'+path);done(null,error);reject(error);return;}
        new STLLoader(loadingManager).load(path,geometry=>{done(new THREE.Mesh(geometry));finished++;showProgress();resolve();},undefined,error=>{done(null,error);reject(error);});
      }));
    };
    try{loaded=await loader.loadAsync(entry.urdf_url);
      const meshResults=await Promise.allSettled(meshLoads),failedMesh=meshResults.find(result=>result.status==='rejected');
      if(failedMesh)throw failedMesh.reason;
      if(disposed||token!==loadToken){disposeModel(loaded);return api;}
      if(robot){scene.remove(robot);disposeModel(robot);}robot=loaded;scene.add(robot);
      const oldMaterials=new Set();
      const modelMaterial=new THREE.MeshStandardMaterial({color:0x8492a4,roughness:.56,metalness:.25});
      robot.traverse(o=>{if(o.isMesh){for(const m of(Array.isArray(o.material)?o.material:[o.material]))if(m)oldMaterials.add(m);o.material=modelMaterial;o.castShadow=false;o.receiveShadow=false;}});
      oldMaterials.forEach(m=>m.dispose());
      setJoints(demoPose(0));robot.updateMatrixWorld(true);bounds.setFromObject(robot);
      if(bounds.isEmpty()||!Number.isFinite(bounds.min.z)||!Number.isFinite(bounds.max.z))throw new Error('原生模型的 mesh 或姿態範圍無效。');
      robot.position.z-=bounds.min.z;
      fit();container.querySelectorAll('.loading-model').forEach(element=>element.remove());renderer.render(scene,camera);container.dataset.modelReady='true';container.dataset.robotType=entry.id;container.dataset.meshCount=String(meshLoads.length);onStatus(`${entry.name||entry.label||entry.id} · 原生 URDF 幾何預覽`);return api;
    }catch(error){if(loaded){scene.remove(loaded);disposeModel(loaded);if(loaded===robot)robot=null;}onStatus('模型載入失敗：'+error.message);throw error;}
  }
  function animate(now){
    if(disposed)return;frame=requestAnimationFrame(animate);
    if(document.hidden||!visible||!hasSize||renderingPaused){last=0;return;}
    if(now-last<1000/30)return;
    const delta=last?Math.min((now-last)/1000,.1):0;last=now;
    if(playing&&robot){time+=delta;setJoints(demoPose(time));}
    controls.update();renderer.render(scene,camera);
  }
  const api={loadRobot,setJoints,setRenderingPaused(value){renderingPaused=!!value;last=0;return api;},setMotion(name,t){motion=name;if(Number.isFinite(t))time=t;if(robot)setJoints(demoPose(time));return api;},setPlaying(value){playing=!!value;return api;},setLandmark,
    getRobot(){return robot;},capture(){renderer.render(scene,camera);return renderer.domElement.toDataURL('image/png');},
    dispose(){if(disposed)return;disposed=true;loadToken++;cancelAnimationFrame(frame);observer.disconnect();visibilityObserver.disconnect();controls.dispose();disposeModel(robot);scene.traverse(o=>{o.geometry?.dispose();if(o.material&&!Array.isArray(o.material))o.material.dispose();});renderer.dispose();renderer.domElement.remove();}};
  frame=requestAnimationFrame(animate);return api;
}
