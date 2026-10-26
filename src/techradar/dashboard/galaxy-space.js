/* Perspective geometry, shared by the scene and the Node regression tests. */
(function (root) {
  'use strict';
  function rotate(v, yaw, pitch) {
    const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
    const x = cy * v.x - sy * v.z, z = sy * v.x + cy * v.z;
    return {x, y:cp * v.y - sp * z, z:sp * v.y + cp * z};
  }
  function unrotate(v, yaw, pitch) {
    const cp = Math.cos(pitch), sp = Math.sin(pitch), cy = Math.cos(yaw), sy = Math.sin(yaw);
    const y = cp * v.y + sp * v.z, z = -sp * v.y + cp * v.z;
    return {x:cy * v.x + sy * z, y, z:-sy * v.x + cy * z};
  }
  function project(v, camera, viewport) {
    const r = rotate({x:v.x - camera.tx, y:v.y - camera.ty, z:v.z - camera.tz}, camera.yaw, camera.pitch);
    const depth = camera.distance - r.z;
    if (depth <= 35) return {visible:false, depth, scale:0, x:0, y:0};
    const scale = viewport.focal / depth;
    return {visible:true, x:viewport.cx + r.x * scale + (camera.panX || 0),
      y:viewport.cy + r.y * scale + (camera.panY || 0), depth, scale};
  }
  function localToWorld(g, x, y, z) {
    const cr = Math.cos(g.roll), sr = Math.sin(g.roll);
    const r = rotate({x:x * cr - y * sr, y:x * sr + y * cr, z}, g.tiltY, g.tiltX);
    return {x:g.x + r.x, y:g.y + r.y, z:g.z + r.z};
  }
  function starRadius(score, projectionScale = 1, scored = true) {
    const value = Number(score);
    const normalized = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) / 100 : 0;
    // A shared score scale across all galaxies; no extra size for core/selection.
    const base = scored === false ? 1 : 1.1 + 4.2 * Math.pow(normalized, 1.8);
    const scale = Number.isFinite(projectionScale) ? Math.max(0, projectionScale) : 1;
    // Let distant bodies recede into their galaxy instead of forming large beads.
    const perspective = Math.max(.38, Math.min(3, Math.sqrt(scale)));
    return base * perspective;
  }
  function galaxyShape(random) {
    const phase=random()*Math.PI*2;
    return {phase, axis:.72+random()*.22, thickness:.14+random()*.1,
      clumps:Array.from({length:4+Math.floor(random()*4)},()=> {
        const angle=random()*Math.PI*2, radius=.25+random()*.48;
        return {x:Math.cos(angle)*radius,y:Math.sin(angle)*radius,z:(random()-.5)*.2,width:.1+random()*.12};
      })};
  }
  function galaxySample(random, shape = {}) {
    const {bulgeFraction = .28, axis = .85, thickness = .2, clumps = [], phase = 0} = shape;
    const bulge = random() < bulgeFraction;
    const noise=()=>random()+random()+random()+random()-2;
    let x,y,z;
    if (!bulge && clumps.length && random()<.48) {
      const c=clumps[Math.floor(random()*clumps.length)];
      x=c.x+noise()*c.width; y=c.y+noise()*c.width;
      z=c.z+noise()*thickness*.65;
    } else {
      const angle=random()*Math.PI*2;
      const radius=bulge ? .4*Math.pow(random(),.8) : 1.03*Math.sqrt(random());
      const edge=1+.055*Math.sin(angle*3+phase)+.035*Math.cos(angle*5-phase);
      x=Math.cos(angle)*radius*edge; y=Math.sin(angle)*radius*edge;
      z=noise()*(bulge?.26:thickness);
    }
    const radius=Math.hypot(x,y), bound=Math.min(1,1.13/Math.max(radius,.001));
    return {x:x*bound,y:y*bound*axis,z,radius:Math.min(radius,1.13),bulge};
  }
  function rankedGalaxySamples(items, random, shape = {}) {
    const ranked=items.map((item,index)=>({index,score:item.scored===false || item.score==null || !Number.isFinite(Number(item.score))
      ? -Infinity : Math.max(0,Math.min(100,Number(item.score)))}))
      .sort((a,b)=>b.score-a.score || a.index-b.index);
    const samples=new Array(items.length), hasScores=ranked.some(item=>Number.isFinite(item.score));
    ranked.forEach((item,rank)=> {
      const sample=galaxySample(random,shape);
      if (hasScores) {
        // Use a continuous radius by score rank, allowing equal scores to spread.
        // Normalize all XYZ components, so depth cannot reverse the score order.
        const radius=rank===0 ? 0 : .1+.98*Math.pow(rank/Math.max(1,items.length-1),.85);
        const length=Math.hypot(sample.x,sample.y,sample.z);
        const direction=length>1e-8 ? sample : {x:1,y:0,z:0};
        const scale=radius/(length>1e-8 ? length : 1);
        samples[item.index]={...sample,x:direction.x*scale,y:direction.y*scale,z:direction.z*scale,radius};
      } else samples[item.index]=sample;
    });
    return samples;
  }
  function zoomCamera(camera, factor, viewport, {minDistance=60,maxDistance=10000,anchor=null} = {}) {
    const distance=Math.max(minDistance,Math.min(maxDistance,camera.distance/factor));
    const result={...camera,distance};
    if (anchor && viewport.focal>0) {
      const delta=(camera.distance-distance)/viewport.focal;
      const shift=unrotate({x:(anchor.x-viewport.cx-(camera.panX||0))*delta,
        y:(anchor.y-viewport.cy-(camera.panY||0))*delta,z:0},camera.yaw,camera.pitch);
      result.tx+=shift.x; result.ty+=shift.y; result.tz+=shift.z;
    }
    return result;
  }
  function fitCamera(points, viewport, yaw = -.25, pitch = .24) {
    if (!points.length) points = [{x:-500,y:-300,z:-150}, {x:500,y:300,z:150}];
    const values = key => points.map(p => p[key]);
    const center = key => (Math.min(...values(key)) + Math.max(...values(key))) / 2;
    const camera = {tx:center('x'), ty:center('y'), tz:center('z'), yaw, pitch, distance:100, panX:0, panY:0};
    const halfX = Math.max(40, viewport.cx - 45), top = Math.max(45, viewport.cy - 122);
    const bottom = Math.max(45, viewport.height - 82 - viewport.cy);
    for (const p of points) {
      const r = rotate({x:p.x-camera.tx,y:p.y-camera.ty,z:p.z-camera.tz}, yaw, pitch);
      camera.distance = Math.max(camera.distance, r.z + 55,
        r.z + Math.abs(r.x) * viewport.focal / halfX,
        r.z + Math.abs(r.y) * viewport.focal / (r.y < 0 ? top : bottom));
    }
    camera.distance *= 1.025;
    return camera;
  }
  const api = {rotate, unrotate, project, localToWorld, fitCamera, starRadius, galaxyShape, galaxySample, rankedGalaxySamples, zoomCamera};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.GalaxySpace = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
