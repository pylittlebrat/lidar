const test = require('node:test');
const assert = require('node:assert/strict');
const space = require('../src/techradar/dashboard/galaxy-space.js');
const viewport = {width:900,height:600,cx:450,cy:320,focal:850};
const camera = {tx:0,ty:0,tz:0,yaw:0,pitch:0,distance:1200,panX:0,panY:0};

function seededRandom(seed) {
  let n=seed;
  return ()=>{n=Math.imul(n,1664525)+1013904223|0;return (n>>>0)/4294967296;};
}
test('galaxy clouds have a dense center and fill every direction without prescribed arms', () => {
  const rng=seededRandom(7421), shape=space.galaxyShape(rng);
  const samples=Array.from({length:5000},()=>space.galaxySample(rng,shape));
  const inner=samples.filter(p=>p.radius<.32).length/samples.length;
  assert.ok(inner>.2&&inner<.6);
  const sectors=Array(8).fill(0);
  for (const p of samples.filter(p=>p.radius>.4)) {
    const angle=Math.atan2(p.y/shape.axis,p.x)+Math.PI;
    sectors[Math.min(7,Math.floor(angle/Math.PI*4))]++;
  }
  assert.ok(sectors.every(count=>count>70));
  const sameRng=seededRandom(7421), sameShape=space.galaxyShape(sameRng);
  assert.deepEqual(space.galaxySample(sameRng,sameShape),samples[0]);
});
test('galaxy clouds stay bounded and have visible volume on both sides of their plane', () => {
  const rng=seededRandom(1337), shape=space.galaxyShape(rng);
  const samples=Array.from({length:5000},()=>space.galaxySample(rng,shape));
  for (const p of samples) {
    assert.ok(Number.isFinite(p.x)&&Number.isFinite(p.y)&&Number.isFinite(p.z));
    assert.ok(Math.hypot(p.x,p.y/shape.axis)<1.131&&Math.abs(p.z)<.65);
  }
  assert.ok(samples.some(p=>p.z>.25)&&samples.some(p=>p.z<-.25));
});

test('score order determines 3D distance while unscored items stay beyond scored zero', () => {
  const rng=seededRandom(834), shape=space.galaxyShape(rng);
  const items=[{score:20},{score:80},{score:100},{score:80},{score:0},
    {score:0,scored:false},{score:null},{score:NaN}];
  const points=space.rankedGalaxySamples(items,rng,shape);
  const distance=i=>Math.hypot(points[i].x,points[i].y,points[i].z);
  assert.equal(points.length,items.length);
  assert.equal(distance(2),0);
  assert.ok(distance(2)<distance(1)&&distance(2)<distance(3));
  assert.ok(Math.max(distance(1),distance(3))<distance(0));
  assert.ok(distance(0)<distance(4));
  for (const i of [5,6,7]) assert.ok(distance(4)<distance(i));
  assert.ok(points.every(p=>[p.x,p.y,p.z].every(Number.isFinite)));
  assert.deepEqual(space.rankedGalaxySamples([],rng,shape),[]);
  const unscored=space.rankedGalaxySamples(Array.from({length:20},()=>({scored:false})),rng,shape);
  assert.ok(unscored.every(p=>[p.x,p.y,p.z].every(Number.isFinite)));
  assert.ok(new Set(unscored.map(p=>Math.hypot(p.x,p.y,p.z))).size>10);
});

test('scene layout keeps every item and preserves score ordering after world-space tilt', () => {
  const source=require('node:fs').readFileSync(require('node:path').join(__dirname,'../src/techradar/dashboard/galaxy-scene.js'),'utf8');
  const Scene=require('node:vm').runInNewContext(source+'\nGalaxyScene;',{GalaxySpace:space});
  const items=[15,80,100,80,0].map((score,id)=>({id:String(id),score,scored:true}));
  items.push({id:'unscored',score:0,scored:false});
  const g={category:'simulation_engine',hue:204,count:items.length,items};
  const scene=Object.create(Scene.prototype);
  scene.data={galaxies:[g]}; scene.nebulaTexture=scene.starTexture=()=>null;
  scene.build();
  assert.equal(scene.byItem.size,items.length);
  assert.equal(scene.particles.filter(p=>p.node).length,items.length);
  const distance=n=>Math.hypot(n.position.x-g.x,n.position.y-g.y,n.position.z-g.z);
  for (const a of items) for (const b of items) {
    if (a.scored&&b.scored&&a.score>b.score) assert.ok(distance(a)<distance(b));
  }
  assert.equal(distance(items[2]),0);
  assert.ok(distance(items[4])<distance(items[5]));
  assert.ok(items.some(n=>Math.abs(n.position.z-g.z)>1));
});

test('higher scores always have larger bodies at the same viewing depth', () => {
  for (const scale of [.1,.5,1,4]) {
    for (let score=1;score<=100;score++) {
      assert.ok(space.starRadius(score,scale)>space.starRadius(score-1,scale));
    }
    assert.ok(space.starRadius(80,scale)>space.starRadius(40,scale)*1.8);
    assert.ok(space.starRadius(100,scale)<16);
  }
});
test('unscored stars remain visible and malformed scores cannot break rendering', () => {
  assert.ok(space.starRadius(null,.1,false)>0);
  assert.ok(space.starRadius(null,1,false)<space.starRadius(0,1));
  for (const score of [undefined,null,NaN,Infinity,-100,1000]) {
    for (const scale of [-1,0,NaN,Infinity,1000]) {
      const radius=space.starRadius(score,scale);
      assert.ok(Number.isFinite(radius)&&radius>0&&radius<16);
    }
  }
});

test('perspective makes nearer points larger and farther from the optical center', () => {
  const far=space.project({x:150,y:80,z:-200},camera,viewport);
  const near=space.project({x:150,y:80,z:200},camera,viewport);
  assert.ok(near.scale>far.scale && near.x>far.x && near.y>far.y);
  assert.ok(space.starRadius(80,.2)<space.starRadius(80,1)*.5);
  assert.ok(space.starRadius(80,9)>space.starRadius(80,1)*2.5);
});

test('deep zoom continues past the former limit and remains bounded', () => {
  const radius=180;
  const zoomed=space.zoomCamera(camera,100,viewport,{minDistance:radius*.42,maxDistance:3000});
  assert.equal(zoomed.distance,radius*.42);
  assert.ok(zoomed.distance<radius*1.65/3);
  assert.equal(space.zoomCamera(camera,.001,viewport,{maxDistance:3000}).distance,3000);
});

test('wheel zoom keeps the point under the cursor stationary with an orbited camera', () => {
  const c={...camera,yaw:.4,pitch:-.3,panX:23,panY:-17}, anchor={x:620,y:210};
  const offset=space.unrotate({x:(anchor.x-viewport.cx-c.panX)*c.distance/viewport.focal,
    y:(anchor.y-viewport.cy-c.panY)*c.distance/viewport.focal,z:0},c.yaw,c.pitch);
  const point={x:c.tx+offset.x,y:c.ty+offset.y,z:c.tz+offset.z};
  const after=space.project(point,space.zoomCamera(c,2.4,viewport,{anchor}),viewport);
  assert.ok(after.visible);
  assert.ok(Math.abs(after.x-anchor.x)<1e-8&&Math.abs(after.y-anchor.y)<1e-8);
});
test('orbit changes both apparent position and depth', () => {
  const p={x:150,y:40,z:200};
  const a=space.project(p,camera,viewport);
  const b=space.project(p,{...camera,yaw:Math.PI/2,pitch:.3},viewport);
  assert.notEqual(a.x,b.x); assert.notEqual(a.y,b.y); assert.notEqual(a.depth,b.depth);
});
test('points on or behind the near plane are clipped without infinities', () => {
  for (const z of [1165,1200,1500]) {
    const p=space.project({x:0,y:0,z},camera,viewport);
    assert.equal(p.visible,false); assert.ok(Number.isFinite(p.x)&&Number.isFinite(p.y));
  }
});
test('rotated screen-plane vectors can be transformed back for spatial operations', () => {
  const v={x:23,y:-17,z:50}, r=space.rotate(v,.61,-.47), result=space.unrotate(r,.61,-.47);
  for (const axis of ['x','y','z']) assert.ok(Math.abs(result[axis]-v[axis])<1e-10);
});
test('a galaxy disk has a world-space tilt and preserves its volume thickness', () => {
  const g={x:100,y:50,z:-200,roll:.3,tiltX:.8,tiltY:.4};
  const a=space.localToWorld(g,40,20,0), b=space.localToWorld(g,40,20,8);
  assert.notEqual(a.z,g.z);
  assert.ok(Math.abs(Math.hypot(b.x-a.x,b.y-a.y,b.z-a.z)-8)<1e-10);
});
test('fit camera frames all scene corners in wide and narrow canvases', () => {
  const points=[];
  for (const x of [-650,650]) for (const y of [-400,400]) for (const z of [-350,350]) points.push({x,y,z});
  for (const [width,height] of [[900,600],[330,580]]) {
    const v={width,height,cx:width/2,cy:(height+40)/2,focal:Math.max(width,height)*.95};
    const c=space.fitCamera(points,v);
    for (const p of points.map(p=>space.project(p,c,v))) {
      assert.ok(p.visible); assert.ok(p.x>=40&&p.x<=width-40); assert.ok(p.y>=120&&p.y<=height-80);
    }
  }
});
