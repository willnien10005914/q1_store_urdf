import * as THREE from './vendor/three/three.module.js';
import {OrbitControls} from './vendor/three/examples/jsm/controls/OrbitControls.js';
import {STLLoader} from './vendor/three/examples/jsm/loaders/STLLoader.js';
import URDFLoader from './vendor/urdf-loader/URDFLoader.js';
import {tr,L} from './i18n.js';

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
  renderer.domElement.setAttribute('aria-label',tr('原生 URDF 關節幾何預覽，可拖曳旋轉','Native URDF joint preview; drag to orbit'));
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
  let explodeAmount=0,explodeAnim=null,modelMaterial=null,accessoriesGroup=null,plantedRootZ=0;
  const linkBase=new Map(); // link name -> rest local position {x,y,z}
  const linkExplodeLocal=new Map(); // link name -> parent-local unit offset direction (baked at rest)
  const _parentInv=new THREE.Matrix4();
  const _worldOffset=new THREE.Vector3();
  const _tmpWorld=new THREE.Vector3();
  const _rootWorld=new THREE.Vector3();
  const SKINS={
    steel:{color:0x8492a4,roughness:.56,metalness:.25},
    carbon:{color:0x1f2933,roughness:.35,metalness:.55},
    gold:{color:0xc9a227,roughness:.4,metalness:.7},
    matte_black:{color:0x111827,roughness:.9,metalness:.05},
    signal_orange:{color:0xe8590c,roughness:.55,metalness:.15},
  };
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
  /** Keep the lowest mesh point on the grid (z=0) so feet rest on the ground plane. */
  function plantFeetOnGround({remember=true}={}){
    if(!robot)return;
    robot.updateMatrixWorld(true);
    bounds.setFromObject(robot);
    if(bounds.isEmpty()||!Number.isFinite(bounds.min.z))return;
    robot.position.z-=bounds.min.z;
    robot.updateMatrixWorld(true);
    if(remember)plantedRootZ=robot.position.z;
  }
  function fit(){
    if(!robot)return;robot.updateMatrixWorld(true);bounds.setFromObject(robot);
    const size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
    const height=Math.max(.5,size.z);
    // Aim near mid-torso but bias up from the feet so the ground line sits in the lower frame.
    target.set(center.x,center.y,bounds.min.z+height*0.42);
    const distance=Math.max(height,size.x/Math.max(.5,camera.aspect))/(2*Math.tan(camera.fov*Math.PI/360)*.72);
    const direction=new THREE.Vector3(1.05,-1.65,.42).normalize();
    controls.target.copy(target);camera.position.copy(direction.multiplyScalar(distance).add(target));controls.update();
    onMetrics({height_m:size.z,width_m:size.x,depth_m:size.y,joints:Object.values(robot.joints).filter(j=>j.jointType!=='fixed').length});
  }
  function disposeModel(model){const geometries=new Set(),materials=new Set();model?.traverse(o=>{if(o.geometry&&!geometries.has(o.geometry)){o.geometry.dispose();geometries.add(o.geometry);}for(const m of (Array.isArray(o.material)?o.material:[o.material]))if(m&&!materials.has(m)){m.dispose();materials.add(m);}});}
  function resize(){const width=container.clientWidth,height=container.clientHeight;hasSize=width>0&&height>0;if(!hasSize){last=0;return;}renderer.setSize(width,height,false);camera.aspect=width/height;camera.updateProjectionMatrix();}
  const observer=new ResizeObserver(resize);observer.observe(container);resize();
  const visibilityObserver=new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;last=0;});visibilityObserver.observe(container);
  function setJoints(values={},{plant=true}={}){
    if(!robot)return api;
    for(const [name,value]of Object.entries(values)){
      const j=robot.joints[name];if(!j||j.mimicJoint||!Number.isFinite(value))continue;
      const lower=Number.isFinite(j.limit?.lower)?j.limit.lower:-Infinity,upper=Number.isFinite(j.limit?.upper)?j.limit.upper:Infinity;
      robot.setJointValue(name,j.jointType==='continuous'?value:THREE.MathUtils.clamp(value,lower,upper));
    }
    // Re-plant after every pose change so feet stay on the ground (homepage + tutorial).
    if(plant&&explodeAmount<=0.02)plantFeetOnGround();
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
  function loadingLabel(){
    let label=container.querySelector(':scope > .loading-model');
    if(!label){label=document.createElement('div');label.className='loading-model';container.append(label);}
    label.hidden=false;return label;
  }
  async function loadRobot(entry){
    const token=++loadToken;manifest=entry;container.dataset.modelReady='false';
    const showProgress=()=>{const text=`${tr('正在載入 Q1 模型','Loading Q1 model')} ${finished}/${Math.max(started,1)}`;loadingLabel().textContent=text;onStatus(text);};
    const shownAt=performance.now();
    loadingLabel().textContent=tr('正在載入原生 URDF 與 meshes…','Loading native URDF and meshes…');onStatus(loadingLabel().textContent);
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    let loaded;
    const manager=new THREE.LoadingManager();
    const loader=new URDFLoader(manager);loader.parseCollision=false;
    const meshLoads=[];
    let started=0,finished=0;
    // FileLoader shares pending URLs across viewers without accounting for the
    // second LoadingManager. Wait for each actual mesh attachment instead.
    loader.loadMeshCb=(path,loadingManager,done)=>{
      started++;showProgress();
      meshLoads.push(new Promise((resolve,reject)=>{
        if(!/\.stl$/i.test(path)){const error=new Error(tr('不支援的原生 mesh：','Unsupported native mesh: ')+path);done(null,error);reject(error);return;}
        new STLLoader(loadingManager).load(path,geometry=>{done(new THREE.Mesh(geometry));finished++;showProgress();resolve();},undefined,error=>{done(null,error);reject(error);});
      }));
    };
    try{loaded=await loader.loadAsync(entry.urdf_url);
      const meshResults=await Promise.allSettled(meshLoads),failedMesh=meshResults.find(result=>result.status==='rejected');
      if(failedMesh)throw failedMesh.reason;
      if(disposed||token!==loadToken){disposeModel(loaded);return api;}
      if(robot){scene.remove(robot);disposeModel(robot);}robot=loaded;scene.add(robot);
      const oldMaterials=new Set();
      modelMaterial=new THREE.MeshStandardMaterial({color:0x8492a4,roughness:.56,metalness:.25});
      robot.traverse(o=>{if(o.isMesh){for(const m of(Array.isArray(o.material)?o.material:[o.material]))if(m)oldMaterials.add(m);o.material=modelMaterial;o.castShadow=false;o.receiveShadow=false;}});
      oldMaterials.forEach(m=>m.dispose());
      robot.position.set(0,0,0);
      clearAccessories();
      setJoints(demoPose(0),{plant:false});
      robot.updateMatrixWorld(true);bounds.setFromObject(robot);
      if(bounds.isEmpty()||!Number.isFinite(bounds.min.z)||!Number.isFinite(bounds.max.z))throw new Error(tr('原生模型的 mesh 或姿態範圍無效。','Native model mesh or pose bounds are invalid.'));
      plantFeetOnGround({remember:true});
      // Bake rest local poses + explode directions AFTER the standing pose is planted.
      captureLinkBases();
      bakeExplodeDirections();
      applyExplode(explodeAmount);
      fit();const remain=Math.max(0,450-(performance.now()-shownAt));if(remain)await new Promise(resolve=>setTimeout(resolve,remain));if(disposed||token!==loadToken)return api;container.querySelectorAll('.loading-model').forEach(element=>element.remove());renderer.render(scene,camera);container.dataset.modelReady='true';container.dataset.robotType=entry.id;container.dataset.meshCount=String(meshLoads.length);onStatus(`${entry.name||entry.label||entry.id} · ${tr('原生 URDF 幾何預覽','native URDF geometry preview')}`);return api;
    }catch(error){if(loaded){scene.remove(loaded);disposeModel(loaded);if(loaded===robot)robot=null;}onStatus(tr('模型載入失敗：','Model load failed: ')+error.message);throw error;}
  }
  function animate(now){
    if(disposed)return;frame=requestAnimationFrame(animate);
    if(document.hidden||!visible||!hasSize||renderingPaused){last=0;return;}
    if(now-last<1000/30)return;
    const delta=last?Math.min((now-last)/1000,.1):0;last=now;
    if(playing&&robot){time+=delta;setJoints(demoPose(time));}
    if(explodeAnim&&robot){
      explodeAnim.elapsed+=delta;
      const u=Math.min(1,explodeAnim.elapsed/Math.max(0.05,explodeAnim.duration));
      const eased=0.5-0.5*Math.cos(Math.PI*u);
      applyExplode(explodeAnim.from+(explodeAnim.to-explodeAnim.from)*eased,{keepAnim:true});
      if(u>=1){explodeAnim=null;onStatus(tr('爆炸圖就緒','Explode view ready'));}
    }
    controls.update();renderer.render(scene,camera);
  }
  /** Movable, non-mimic joints with their URDF limits and current angle, in URDF order. */
  function getJointStates(){
    if(!robot)return [];
    return Object.values(robot.joints).filter(j=>j.jointType!=='fixed'&&!j.mimicJoint).map(j=>{
      const continuous=j.jointType==='continuous';
      const lower=continuous?-Math.PI:Number.isFinite(j.limit?.lower)?j.limit.lower:-Math.PI,upper=continuous?Math.PI:Number.isFinite(j.limit?.upper)?j.limit.upper:Math.PI;
      const value=Array.isArray(j.jointValue)?j.jointValue[0]:Number(j.angle)||0;
      return {name:j.name,type:j.jointType,lower,upper,value:Number.isFinite(value)?value:0};
    });
  }
  function captureLinkBases(){
    linkBase.clear();
    if(!robot?.links)return;
    for(const [name,link] of Object.entries(robot.links)){
      linkBase.set(name,{x:link.position.x,y:link.position.y,z:link.position.z});
    }
  }
  function resetLinksToBase(){
    if(!robot?.links)return;
    for(const [name,link] of Object.entries(robot.links)){
      const base=linkBase.get(name);if(!base)continue;
      link.position.set(base.x,base.y,base.z);
    }
  }
  /** Bake parent-local explode directions once at the assembled rest pose (stable 0→1 lerp). */
  function bakeExplodeDirections(){
    linkExplodeLocal.clear();
    if(!robot?.links||!linkBase.size)return;
    resetLinksToBase();
    robot.position.z=plantedRootZ;
    robot.updateMatrixWorld(true);
    const root=robot.links.center_torso_link||robot.links.center_hip_link||Object.values(robot.links)[0];
    root?.getWorldPosition(_rootWorld);
    for(const [name,link] of Object.entries(robot.links)){
      if(!linkBase.has(name))continue;
      link.getWorldPosition(_tmpWorld);
      _worldOffset.copy(_tmpWorld).sub(_rootWorld);
      // Prefer lateral / slight upward separation; damp downward so feet don't dive.
      if(_worldOffset.z<0)_worldOffset.z*=0.2;
      _worldOffset.z+=0.1;
      if(_worldOffset.lengthSq()<1e-10)_worldOffset.set(0,0,0.25);
      else _worldOffset.normalize();
      // Convert world-space unit direction into the link parent's local axes.
      if(link.parent){
        _parentInv.copy(link.parent.matrixWorld).invert();
        _worldOffset.transformDirection(_parentInv);
      }
      if(_worldOffset.lengthSq()<1e-10)_worldOffset.set(0,0,0.25);
      else _worldOffset.normalize();
      linkExplodeLocal.set(name,{x:_worldOffset.x,y:_worldOffset.y,z:_worldOffset.z});
    }
  }
  function applyExplode(amount=0,{keepAnim=false}={}){
    if(!keepAnim)explodeAnim=null; // scrubbing the slider cancels an in-flight animation
    explodeAmount=Math.max(0,Math.min(1,Number(amount)||0));
    if(!robot?.links||!linkBase.size)return api;
    if(!linkExplodeLocal.size)bakeExplodeDirections();
    // Always start from the assembled rest pose, then lerp a fixed local offset.
    // (Previously directions were recomputed from already-exploded world poses → center jumped.)
    resetLinksToBase();
    const scale=explodeAmount*0.55;
    if(scale>1e-6){
      for(const [name,link] of Object.entries(robot.links)){
        const base=linkBase.get(name);const dir=linkExplodeLocal.get(name);
        if(!base||!dir)continue;
        link.position.set(base.x+dir.x*scale,base.y+dir.y*scale,base.z+dir.z*scale);
      }
    }
    // Keep root Z locked to the planted standing height (plus a tiny float when open).
    // Re-running plantFeetOnGround() each frame made the whole model hop as AABB changed.
    robot.position.z=plantedRootZ+explodeAmount*0.04;
    return api;
  }
  function animateExplode(to=0.85,{duration=1.2,from=explodeAmount}={}){
    if(!linkExplodeLocal.size)bakeExplodeDirections();
    explodeAnim={from:Number(from)||0,to:Math.max(0,Math.min(1,Number(to)||0)),elapsed:0,duration:Math.max(0.2,Number(duration)||1.2)};
    onStatus(tr('播放爆炸圖…','Playing explode…'));
    return api;
  }
  function setSkin(name='steel'){
    const skin=SKINS[name]||SKINS.steel;
    if(!modelMaterial)modelMaterial=new THREE.MeshStandardMaterial();
    modelMaterial.color.setHex(skin.color);
    modelMaterial.roughness=skin.roughness;
    modelMaterial.metalness=skin.metalness;
    modelMaterial.needsUpdate=true;
    if(robot)robot.traverse(o=>{if(o.isMesh&&o.material===modelMaterial){/* shared */}});
    return api;
  }
  function clearAccessories(){
    if(!accessoriesGroup)return;
    accessoriesGroup.traverse(o=>{o.geometry?.dispose();if(o.material&&!Array.isArray(o.material))o.material.dispose();});
    accessoriesGroup.parent?.remove(accessoriesGroup);
    accessoriesGroup=null;
  }
  function parentLink(preferred='center_torso_link'){
    if(!robot?.links)return null;
    return robot.links[preferred]||robot.links.center_waist_pitch_link||robot.links.center_hip_link||Object.values(robot.links)[0]||null;
  }
  function setAccessories(cfg={}){
    if(!robot)return api;
    clearAccessories();
    accessoriesGroup=new THREE.Group();
    accessoriesGroup.name='q1-hardware-accessories';
    const parent=parentLink(cfg.parentLink||'center_torso_link');
    if(!parent)return api;
    parent.add(accessoriesGroup);
    if(cfg.tof?.enabled!==false&&cfg.tof){
      const s=Math.max(0.01,Number(cfg.tof.size)||0.04);
      const mat=new THREE.MeshStandardMaterial({color:0x22b8cf,roughness:.4,metalness:.35,emissive:0x0b7285,emissiveIntensity:.25});
      const body=new THREE.Mesh(new THREE.BoxGeometry(s,s*0.6,s*0.35),mat);
      const lens=new THREE.Mesh(new THREE.CylinderGeometry(s*0.18,s*0.18,s*0.2,16),new THREE.MeshStandardMaterial({color:0x111827,metalness:.8,roughness:.2}));
      lens.rotation.x=Math.PI/2;lens.position.z=s*0.22;
      const tof=new THREE.Group();tof.name='tof';tof.add(body,lens);
      tof.position.set(Number(cfg.tof.x)||0,Number(cfg.tof.y)||0,Number(cfg.tof.z)||0);
      accessoriesGroup.add(tof);
    }
    if(cfg.audio?.enabled!==false&&cfg.audio){
      const s=Math.max(0.01,Number(cfg.audio.size)||0.035);
      const mat=new THREE.MeshStandardMaterial({color:0xf59f00,roughness:.45,metalness:.2});
      const grille=new THREE.Mesh(new THREE.CylinderGeometry(s*0.45,s*0.45,s*0.25,24),mat);
      grille.rotation.x=Math.PI/2;
      const mic=new THREE.Mesh(new THREE.SphereGeometry(s*0.22,12,12),new THREE.MeshStandardMaterial({color:0xffec99,metalness:.1,roughness:.6}));
      mic.position.set(s*0.55,0,0);
      const audio=new THREE.Group();audio.name='audio';audio.add(grille,mic);
      audio.position.set(Number(cfg.audio.x)||0,Number(cfg.audio.y)||0,Number(cfg.audio.z)||0);
      accessoriesGroup.add(audio);
    }
    onStatus(tr('已更新感測器／音訊外形','Accessories regenerated'));
    return api;
  }
  function setCameraPreset(name='three_quarter'){
    if(!robot){fit();return api;}
    robot.updateMatrixWorld(true);bounds.setFromObject(robot);
    const center=bounds.getCenter(new THREE.Vector3());
    const size=bounds.getSize(new THREE.Vector3());
    const height=Math.max(.5,size.z);
    const distance=Math.max(height,size.x/Math.max(.5,camera.aspect))/(2*Math.tan(camera.fov*Math.PI/360)*.77);
    const dirs={
      three_quarter:new THREE.Vector3(1,-1.7,.48),
      front:new THREE.Vector3(0,-1,.15),
      side:new THREE.Vector3(1,0,.12),
      top:new THREE.Vector3(.15,-.15,1),
      detail_torso:new THREE.Vector3(.55,-.9,.35),
    };
    const direction=(dirs[name]||dirs.three_quarter).clone().normalize();
    const dist=name==='detail_torso'?distance*0.55:distance;
    controls.target.copy(center);
    if(name==='detail_torso')controls.target.z=center.z+size.z*0.15;
    camera.position.copy(direction.multiplyScalar(dist).add(controls.target.clone()));
    controls.update();
    return api;
  }
  function regenerateAppearance(cfg={}){
    if(cfg.skin)setSkin(cfg.skin);
    setAccessories({
      tof:cfg.tof,
      audio:cfg.audio,
      parentLink:cfg.parentLink||'center_torso_link',
    });
    if(cfg.camera)setCameraPreset(cfg.camera);
    if(Number.isFinite(cfg.explode))applyExplode(cfg.explode);
    onStatus(tr('已重新生成機器人外形','Robot appearance regenerated'));
    return api;
  }
  const api={loadRobot,setJoints,getJointStates,setRenderingPaused(value){renderingPaused=!!value;last=0;return api;},setMotion(name,t){motion=name;if(Number.isFinite(t))time=t;if(robot)setJoints(demoPose(time));return api;},setPlaying(value){playing=!!value;return api;},setLandmark,
    setExplode:applyExplode,animateExplode,setSkin,setAccessories,setCameraPreset,regenerateAppearance,getExplode(){return explodeAmount;},
    getRobot(){return robot;},capture(){renderer.render(scene,camera);return renderer.domElement.toDataURL('image/png');},
    dispose(){if(disposed)return;disposed=true;loadToken++;explodeAnim=null;cancelAnimationFrame(frame);observer.disconnect();visibilityObserver.disconnect();controls.dispose();clearAccessories();disposeModel(robot);scene.traverse(o=>{o.geometry?.dispose();if(o.material&&!Array.isArray(o.material))o.material.dispose();});renderer.dispose();renderer.domElement.remove();}};
  frame=requestAnimationFrame(animate);return api;
}
