/* Deterministic XYZ galaxies with a perspective orbit camera, rendered locally. */
class GalaxyScene {
  constructor({canvas, data, tooltip, onPick, onState, reducedMotion, matchNode}) {
    Object.assign(this, {canvas, data, tooltip, onPick, onState, reducedMotion, matchNode});
    this.ctx = canvas.getContext('2d'); this.stage = canvas.parentElement;
    this.width = this.height = 0; this.viewport = {}; this.fitDistance = 1;
    this.camera = {tx:0,ty:0,tz:0,yaw:-.25,pitch:.24,distance:2200,panX:0,panY:0};
    this.focus = this.selected = this.hover = this.drag = null;
    this.query = ''; this.showRelations = false; this.mode = 'rotate'; this.autoOrbit = false;
    this.frame = this.orbitFrame = 0; this.labelHits = []; this.projected = [];
    this.build(); this.bind();
    this.resizeObserver = new ResizeObserver(() => this.resize()); this.resizeObserver.observe(this.stage);
    this.visible = true;
    this.intersectionObserver = new IntersectionObserver(entries => {
      this.visible = entries[0].isIntersecting;
      if (this.visible && this.autoOrbit) this.startOrbit();
    });
    this.intersectionObserver.observe(canvas);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && this.autoOrbit) this.startOrbit(); });
  }
  random(seed) {
    let n = seed >>> 0;
    return () => { n += 0x6d2b79f5; let t = Math.imul(n ^ n >>> 15, 1 | n); t ^= t + Math.imul(t ^ t >>> 7, 61 | t); return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  }
  hash(s) { let n=2166136261; for (const c of s) n=Math.imul(n ^ c.charCodeAt(0),16777619); return n>>>0; }
  clamp(v,a,b) { return Math.max(a,Math.min(b,v)); }
  color(g,a=1,l=72) { return `hsla(${g.hue},55%,${l}%,${a})`; }
  build() {
    // Each direction occupies a different depth, rather than a tilted flat map.
    const anchors = {
      simulation_engine:[-400,-245,-110], robot_learning:[200,-235,-140],
      real2sim:[100,430,330], sim2real:[-270,350,30], embodied_ai:[120,90,-350],
      simulation_validation:[540,300,-170], manipulation:[-150,25,300],
      world_model:[560,-80,260], asset_generation:[-590,410,200],
      humanoid:[510,-430,-280], system_identification:[-710,-370,-70],
      engineering_machinery:[-730,50,-290], simulation_platform:[-80,-490,50],
      uncategorized:[-200,-480,320]
    };
    this.byCat = new Map(); this.byItem = new Map(); this.particles = []; this.corners = [];
    this.data.galaxies.forEach((g,i) => {
      const rng=this.random(this.hash(g.category)), a=anchors[g.category] || [(i%4)*330-450,Math.floor(i/4)*270-250,(rng()-.5)*700];
      [g.x,g.y,g.z]=a; g.radius=55+Math.sqrt(Math.max(1,g.count))*8;
      g.roll=(rng()-.5)*2.5; g.tiltX=-.2-rng()*.5; g.tiltY=(rng()-.5)*.65;
      g.shape=GalaxySpace.galaxyShape(rng);
      this.byCat.set(g.category,g);
      const positions=GalaxySpace.rankedGalaxySamples(g.items,rng,g.shape);
      g.items.forEach((n,j) => {
        n.galaxy=g; n.index=j;
        const sample=positions[j];
        n.position=GalaxySpace.localToWorld(g,sample.x*g.radius,sample.y*g.radius,sample.z*g.radius);
        this.byItem.set(n.id,n); this.particles.push({g,node:n,position:n.position});
      });
      g.dust=[];
      for (let j=0;j<Math.min(550,210+g.count*2);j++) {
        const sample=GalaxySpace.galaxySample(rng,{...g.shape,bulgeFraction:.3});
        const particle={g,position:GalaxySpace.localToWorld(g,sample.x*g.radius,sample.y*g.radius,sample.z*g.radius),alpha:.08+rng()*.22,radius:.22+rng()*.42};
        g.dust.push(particle); this.particles.push(particle);
      }
      g.outline=Array.from({length:32},(_,j) => GalaxySpace.localToWorld(g,Math.cos(j*Math.PI/16)*g.radius*1.2,Math.sin(j*Math.PI/16)*g.radius*1.2,0));
      this.corners.push(...g.outline,...g.items.map(n=>n.position));
      g.nebula=this.nebulaTexture(g);
      g.starSprite=this.starTexture(g);
    });
    const rng=this.random(35271);
    this.background=Array.from({length:850},()=>({x:(rng()-.5)*13000,y:(rng()-.5)*9500,z:(rng()-.5)*10000,alpha:.12+rng()*.35,radius:.3+rng()*.65}));
  }
  starTexture(g) {
    const tile=document.createElement('canvas'); tile.width=tile.height=64;
    const ctx=tile.getContext('2d'), light=ctx.createRadialGradient(32,32,0,32,32,32);
    light.addColorStop(0,'rgba(255,251,239,1)');
    light.addColorStop(.2,this.color(g,1,93));
    light.addColorStop(.5,this.color(g,.98,76));
    light.addColorStop(.82,this.color(g,.6,64));
    light.addColorStop(1,this.color(g,0,65));
    ctx.fillStyle=light; ctx.fillRect(0,0,64,64);
    return tile;
  }
  nebulaTexture(g) {
    // A cached, transparent cloud layer: no external images or per-frame noise.
    const tile=document.createElement('canvas'); tile.width=tile.height=768;
    const ctx=tile.getContext('2d'), rng=this.random(this.hash(g.category+':nebula'));
    const center=384, unit=256;
    const stamp=document.createElement('canvas'); stamp.width=stamp.height=64;
    const brush=stamp.getContext('2d'), haze=brush.createRadialGradient(32,32,0,32,32,32);
    haze.addColorStop(0,`hsla(${g.hue},65%,65%,.6)`);
    haze.addColorStop(.35,`hsla(${g.hue},65%,52%,.23)`);
    haze.addColorStop(1,`hsla(${g.hue},60%,45%,0)`);
    brush.fillStyle=haze; brush.fillRect(0,0,64,64);
    // Loose, uneven concentrations, with open space between the individual stars.
    for (let i=0;i<2200;i++) {
      const sample=GalaxySpace.galaxySample(rng,{...g.shape,bulgeFraction:.24});
      const radius=24+rng()*36;
      const fade=Math.pow(Math.max(0,1-sample.radius/1.22),.5);
      ctx.globalAlpha=(.022+rng()*.032)*fade;
      ctx.drawImage(stamp,center+sample.x*unit-radius,center+sample.y*unit-radius,radius*2,radius*2);
    }
    ctx.globalAlpha=1;
    // Fine unresolved starlight belongs to the material, never to item counts/hits.
    for (let i=0;i<2800;i++) {
      const sample=GalaxySpace.galaxySample(rng,{...g.shape,bulgeFraction:.36});
      const radius=.15+rng()*.3, alpha=(.08+rng()*.2)*(1-sample.radius/1.2);
      ctx.fillStyle=this.color(g,alpha,85);
      ctx.beginPath(); ctx.arc(center+sample.x*unit,center+sample.y*unit,radius,0,Math.PI*2); ctx.fill();
    }
    const core=ctx.createRadialGradient(center,center,0,center,center,unit*.5);
    core.addColorStop(0,this.color(g,.3,88));
    core.addColorStop(.25,this.color(g,.14,70));
    core.addColorStop(1,this.color(g,0,58));
    ctx.fillStyle=core; ctx.fillRect(center-unit*.5,center-unit*.5,unit,unit);
    return tile;
  }
  project(v) { return GalaxySpace.project(v,this.camera,this.viewport); }
  overviewCamera() { return GalaxySpace.fitCamera(this.corners,this.viewport); }
  zoomLevel() { return this.fitDistance/this.camera.distance; }
  resize() {
    const rect=this.stage.getBoundingClientRect(), oldFocal=this.viewport.focal;
    this.width=rect.width; this.height=rect.height;
    if (!this.width || !this.height) return;
    this.dpr=Math.min(window.devicePixelRatio||1,2);
    this.canvas.width=Math.round(this.width*this.dpr); this.canvas.height=Math.round(this.height*this.dpr);
    this.viewport={width:this.width,height:this.height,cx:this.width/2,cy:(this.height+40)/2,focal:Math.max(this.width,this.height)*.95};
    const home=this.overviewCamera(); this.fitDistance=home.distance;
    if (!oldFocal) this.camera=home;
    else this.camera.distance*=this.viewport.focal/oldFocal;
    this.draw();
  }
  moveCamera(target,smooth=true) {
    cancelAnimationFrame(this.frame); this.tooltip.hidden=true; this.hover=null;
    if (!smooth || this.reducedMotion) { this.camera=target; this.draw(); return; }
    const start={...this.camera}, startAt=performance.now();
    const tick=time => {
      const t=this.clamp((time-startAt)/500,0,1), p=1-Math.pow(1-t,3);
      for (const k of Object.keys(target)) this.camera[k]=start[k]+(target[k]-start[k])*p;
      this.draw(); if (t<1) this.frame=requestAnimationFrame(tick);
    };
    this.frame=requestAnimationFrame(tick);
  }
  setFocus(g,node=null,smooth=true) {
    this.focus=g; this.selected=node; this.query=''; this.setAutoOrbit(false);
    this.moveCamera({...this.camera,tx:g.x,ty:g.y,tz:g.z,panX:0,panY:0,
      distance:Math.max(g.radius*2.1,this.viewport.focal*g.radius*2.1/Math.max(100,Math.min(this.width-100,this.height-190)))},smooth);
  }
  setSelected(node) { this.selected=node; this.setAutoOrbit(false); this.draw(); }
  reset() {
    this.focus=this.selected=this.hover=null; this.query=''; this.setAutoOrbit(false);
    const home=this.overviewCamera(); this.fitDistance=home.distance; this.moveCamera(home);
  }
  zoom(factor,anchor=null) {
    this.setAutoOrbit(false);
    cancelAnimationFrame(this.frame); this.tooltip.hidden=true;
    this.camera=GalaxySpace.zoomCamera(this.camera,factor,this.viewport,{
      minDistance:this.focus ? Math.max(60,this.focus.radius*.42) : 60,
      maxDistance:this.fitDistance*2.2,anchor});
    this.draw();
  }
  setAutoOrbit(value) {
    this.autoOrbit=value; cancelAnimationFrame(this.orbitFrame);
    this.onState?.({autoOrbit:value}); if (value) this.startOrbit();
  }
  startOrbit() {
    cancelAnimationFrame(this.orbitFrame); let last=performance.now();
    const tick=time => {
      if (!this.autoOrbit || !this.visible || document.hidden) return;
      if (time-last>32) { this.camera.yaw+=Math.min(time-last,60)*.000045; last=time; this.draw(); }
      this.orbitFrame=requestAnimationFrame(tick);
    };
    this.orbitFrame=requestAnimationFrame(tick);
  }
  glow(x,y,r,g,alpha) {
    if (!Number.isFinite(r) || r<.1) return;
    const ctx=this.ctx; r=Math.min(r,this.width);
    const gradient=ctx.createRadialGradient(x,y,0,x,y,r);
    gradient.addColorStop(0,this.color(g,alpha,78)); gradient.addColorStop(.3,this.color(g,alpha*.32,62)); gradient.addColorStop(1,this.color(g,0,48));
    ctx.fillStyle=gradient; ctx.beginPath(); ctx.arc(x,y,r,0,Math.PI*2); ctx.fill();
  }
  route(a,b,alpha,weight,from=null) {
    const ctx=this.ctx, start=from?.position||a, end=b;
    const mid={x:(start.x+end.x)/2,y:(start.y+end.y)/2-60,z:(start.z+end.z)/2+100};
    ctx.strokeStyle=this.color(a,alpha); ctx.lineWidth=this.clamp(.4+Math.sqrt(weight)*.09,.5,1.4); ctx.beginPath();
    let pen=false;
    for (let j=0;j<=28;j++) {
      const t=j/28,u=1-t,p=this.project({x:u*u*start.x+2*u*t*mid.x+t*t*end.x,y:u*u*start.y+2*u*t*mid.y+t*t*end.y,z:u*u*start.z+2*u*t*mid.z+t*t*end.z});
      if (!p.visible) { pen=false; continue; }
      if (pen) ctx.lineTo(p.x,p.y); else ctx.moveTo(p.x,p.y); pen=true;
    }
    ctx.stroke();
  }
  text(value,x,y,font,fill) {
    const ctx=this.ctx; ctx.font=font; ctx.textAlign='center'; ctx.lineWidth=4; ctx.lineJoin='round';
    ctx.strokeStyle='#080e19e8'; ctx.strokeText(value,x,y); ctx.fillStyle=fill; ctx.fillText(value,x,y);
  }
  draw() {
    const W=this.width,H=this.height,ctx=this.ctx;
    if (!W||!H) return;
    ctx.setTransform(this.dpr,0,0,this.dpr,0,0); ctx.clearRect(0,0,W,H);
    ctx.fillStyle='#070d18'; ctx.fillRect(0,0,W,H);
    const bg=ctx.createRadialGradient(W*.48,H*.5,0,W*.48,H*.5,W*.75);
    bg.addColorStop(0,'#172a454f'); bg.addColorStop(1,'#07101e00'); ctx.fillStyle=bg; ctx.fillRect(0,0,W,H);
    for (const star of this.background) {
      const p=this.project(star); if (!p.visible||p.x<0||p.x>W||p.y<0||p.y>H) continue;
      ctx.fillStyle=`rgba(171,196,226,${star.alpha})`; ctx.beginPath(); ctx.arc(p.x,p.y,star.radius,0,Math.PI*2); ctx.fill();
    }
    const zoom=this.zoomLevel(), active=this.hover?.galaxy||this.focus;
    let routes=0;
    this.data.bridges.forEach((b,i) => {
      const a=this.byCat.get(b.source),c=this.byCat.get(b.target),hi=active&&(active===a||active===c);
      if (!a||!c||(!this.showRelations&&!hi&&(i>=4||active))) return;
      if (hi) routes++;
      if (zoom>=1.8&&(this.selected||routes>3)&&!this.showRelations) return;
      this.route(a,c,hi ? zoom>=1.8 ? .09 : .28 : active ? .025 : this.showRelations ? .13 : .07,b.weight);
    });
    if (this.selected) for (const cat of this.selected.categories) {
      const g=this.byCat.get(cat); if (g&&g!==this.selected.galaxy) this.route(this.selected.galaxy,g,.48,1,this.selected);
    }
    const groups=this.data.galaxies.map(g=>({g,p:this.project(g)})).filter(o=>o.p.visible).sort((a,b)=>b.p.depth-a.p.depth);
    this.labelHits=[];
    for (const {g,p} of groups) {
      const alpha=this.groupAlpha(g,active), r=g.radius*p.scale;
      if (p.x+r<0||p.x-r>W||p.y+r<0||p.y-r>H) continue;
      // Project the local tangent, so large clouds do not flip or vanish as we enter.
      const sx=(p.x-this.viewport.cx-this.camera.panX)/this.viewport.focal;
      const sy=(p.y-this.viewport.cy-this.camera.panY)/this.viewport.focal;
      const tangent=point=> {
        const v=GalaxySpace.rotate({x:point.x-g.x,y:point.y-g.y,z:point.z-g.z},this.camera.yaw,this.camera.pitch);
        return {x:p.scale*(v.x+sx*v.z),y:p.scale*(v.y+sy*v.z)};
      };
      const u=tangent(GalaxySpace.localToWorld(g,g.radius,0,0)),v=tangent(GalaxySpace.localToWorld(g,0,g.radius,0));
      ctx.save(); ctx.transform(u.x,u.y,v.x,v.y,p.x,p.y);
      ctx.globalAlpha=alpha*Math.min(1,W/Math.max(W,r));
      ctx.globalCompositeOperation='screen';
      ctx.drawImage(g.nebula,-1.5,-1.5,3,3);
      ctx.restore();
    }
    this.projected=this.particles.map(o=>({...o,screen:this.project(o.position)})).filter(o=>o.screen.visible&&o.screen.x>-20&&o.screen.x<W+20&&o.screen.y>-20&&o.screen.y<H+20).sort((a,b)=>b.screen.depth-a.screen.depth);
    for (const o of this.projected) {
      const {g,node:n,screen:p}=o, alpha=this.groupAlpha(g,active), fog=this.clamp(this.camera.distance/p.depth,.38,1.2);
      if (n) {
        n.screen=p;
        const selected=this.selected===n, lit=selected||this.hover?.node===n;
        const radius=GalaxySpace.starRadius(n.score,p.scale,n.scored);
        ctx.globalAlpha=this.clamp(alpha*fog,0,1);
        if (!n.index||lit) this.glow(p.x,p.y,radius*(lit?6:4.5),g,lit ? .7 : .32);
        ctx.drawImage(g.starSprite,p.x-radius,p.y-radius,radius*2,radius*2);
        if (selected) { ctx.strokeStyle='#d5efff'; ctx.lineWidth=1; ctx.beginPath(); ctx.arc(p.x,p.y,radius+6,0,Math.PI*2); ctx.stroke(); }
        ctx.globalAlpha=1;
      } else {
        ctx.fillStyle=this.color(g,this.clamp(o.alpha*alpha*fog,0,1),72);
        ctx.beginPath(); ctx.arc(p.x,p.y,o.radius*this.clamp(Math.sqrt(p.scale)*1.2,.45,2.2),0,Math.PI*2); ctx.fill();
      }
    }
    const occupied=[];
    // Near labels take precedence. Text stays in screen space and remains upright.
    for (const {g,p} of [...groups].reverse().sort((a,b)=>Number(b.g===this.focus)-Number(a.g===this.focus))) {
      if (this.focus&&g!==this.focus&&zoom>1.8) continue;
      const outline=g.outline.map(v=>this.project(v)).filter(v=>v.visible);
      if (!outline.length||p.x<32||p.x>W-32||p.y<100||p.y>H-65) continue;
      const bottom=Math.min(H-60,Math.max(...outline.map(v=>v.y))+19), alpha=this.groupAlpha(g,active);
      const choices=[[p.x,bottom],[p.x,bottom+35],[p.x-65,bottom-12],[p.x+65,bottom-12],[p.x,bottom-45]];
      const position=choices.find(([x,y]) => x>68&&x<W-68&&y>130&&y<H-55&&!occupied.some(b=>this.overlaps({x:x-66,y:y-14,w:132,h:34},b)));
      if (!position) continue;
      const [labelX,labelY]=position,box={x:labelX-66,y:labelY-14,w:132,h:34,g};
      occupied.push(box); this.labelHits.push(box);
      if (labelX!==p.x||labelY!==bottom) {
        ctx.strokeStyle=this.color(g,.24); ctx.lineWidth=.6; ctx.beginPath(); ctx.moveTo(p.x,p.y); ctx.lineTo(labelX,labelY-13); ctx.stroke();
      }
      this.text(g.label,labelX,labelY,`${g===this.focus?14:12}px -apple-system,"Microsoft YaHei",sans-serif`,this.color(g,Math.max(.4,alpha),79));
      this.text(g.count?`${g.count} 条情报`:`${g.tagged_count} 条关联`,labelX,labelY+16,'9px Consolas,"Microsoft YaHei",sans-serif',alpha<.5?'#71849c':'#93a9c2');
    }
    const nodes=[...this.byItem.values()].filter(n=>this.selected===n||this.hover?.node===n||(this.query&&this.matchNode(n))||((this.focus?n.galaxy===this.focus:zoom>2)&&n.index<(zoom>6?6:2)));
    nodes.sort((a,b)=>Number(b===this.selected||b===this.hover?.node)-Number(a===this.selected||a===this.hover?.node)||a.index-b.index);
    for (const n of nodes) {
      const p=this.project(n.position); if (!p.visible||p.x<30||p.x>W-30||p.y<120||p.y>H-65) continue;
      const title=n.label.length>32?n.label.slice(0,31)+'…':n.label;
      ctx.font='10px -apple-system,"Microsoft YaHei",sans-serif'; const width=ctx.measureText(title).width+12;
      const offset=Math.max(12,GalaxySpace.starRadius(n.score,p.scale,n.scored)+9);
      const box={x:p.x-width/2,y:p.y-offset-13,w:width,h:17};
      if (box.x<10||box.x+width>W-10||occupied.some(b=>this.overlaps(box,b))) continue;
      occupied.push(box); this.text(title,p.x,p.y-offset,'10px -apple-system,"Microsoft YaHei",sans-serif',n===this.selected?'#f0f7ff':'#b4c9dd');
    }
    this.axes(); this.onState?.({zoom,level:zoom<1.6?'01 / 全景':zoom<3.4?'02 / 星系':'03 / 情报',autoOrbit:this.autoOrbit});
  }
  groupAlpha(g,active) {
    if (this.query&&!g.label.toLowerCase().includes(this.query)&&!g.items.some(n=>this.matchNode(n))) return .13;
    return active&&active!==g ? this.focus ? .16 : .35 : 1;
  }
  overlaps(a,b) { return a.x<b.x+b.w&&a.x+a.w>b.x&&a.y<b.y+b.h&&a.y+a.h>b.y; }
  axes() {
    const ctx=this.ctx,x=this.width-42,y=this.height-40;
    const axes=[{x:1,y:0,z:0,label:'X',color:'#c29991'},{x:0,y:-1,z:0,label:'Y',color:'#8ab9a0'},{x:0,y:0,z:1,label:'Z',color:'#8ca8cf'}];
    axes.map(v=>({...v,r:GalaxySpace.rotate(v,this.camera.yaw,this.camera.pitch)})).sort((a,b)=>a.r.z-b.r.z).forEach(v=> {
      ctx.strokeStyle=v.color; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(x,y); ctx.lineTo(x+v.r.x*19,y+v.r.y*19); ctx.stroke();
      ctx.font='8px Consolas,monospace'; ctx.fillStyle=v.color; ctx.textAlign='center'; ctx.fillText(v.label,x+v.r.x*26,y+v.r.y*26+3);
    });
  }
  hit(x,y) {
    for (const b of this.labelHits) if (x>=b.x&&x<=b.x+b.w&&y>=b.y&&y<=b.y+b.h) return {galaxy:b.g};
    if (this.focus||this.zoomLevel()>=1.6) {
      let best=null,distance=Infinity;
      for (const o of [...this.projected].reverse()) if (o.node) {
        const d=Math.hypot(o.screen.x-x,o.screen.y-y);
        const target=Math.max(6,GalaxySpace.starRadius(o.node.score,o.screen.scale,o.node.scored)+4);
        if (d<=target&&d/target<distance) { best=o.node; distance=d/target; }
      }
      if (best) return {galaxy:best.galaxy,node:best};
    }
    const groups=this.data.galaxies.map(g=>({g,p:this.project(g)})).filter(o=>o.p.visible).sort((a,b)=>a.p.depth-b.p.depth);
    for (const {g,p} of groups) {
      const points=g.outline.map(v=>this.project(v)).filter(v=>v.visible); let inside=false;
      for (let i=0,j=points.length-1;i<points.length;j=i++) {
        const a=points[i],b=points[j];
        if ((a.y>y)!==(b.y>y)&&x<(b.x-a.x)*(y-a.y)/(b.y-a.y)+a.x) inside=!inside;
      }
      if (inside||Math.hypot(p.x-x,p.y-y)<9) return {galaxy:g};
    }
    return null;
  }
  rotate(dx,dy) { this.camera.yaw+=dx; this.camera.pitch=this.clamp(this.camera.pitch+dy,-1.25,1.25); this.draw(); }
  bind() {
    const canvas=this.canvas,local=e=>{const r=canvas.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};};
    canvas.addEventListener('contextmenu',e=>e.preventDefault());
    canvas.addEventListener('pointerdown',e=> {
      if (![0,1,2].includes(e.button)) return;
      e.preventDefault(); canvas.focus({preventScroll:true}); cancelAnimationFrame(this.frame); this.setAutoOrbit(false);
      canvas.setPointerCapture(e.pointerId);
      this.drag={pointer:e.pointerId,x:e.clientX,y:e.clientY,camera:{...this.camera},moved:false,pan:e.shiftKey||e.button!==0||this.mode==='pan'};
    });
    canvas.addEventListener('pointermove',e=> {
      if (this.drag) {
        const d=this.drag,dx=e.clientX-d.x,dy=e.clientY-d.y;
        if (Math.hypot(dx,dy)>5) d.moved=true;
        if (d.moved) {
          this.hover=null; this.tooltip.hidden=true;
          if (d.pan) { this.camera.panX=d.camera.panX+dx; this.camera.panY=d.camera.panY+dy; }
          else { this.camera.yaw=d.camera.yaw+dx*.006; this.camera.pitch=this.clamp(d.camera.pitch+dy*.005,-1.25,1.25); }
          this.draw();
        }
        return;
      }
      const p=local(e); this.hover=this.hit(p.x,p.y); canvas.style.cursor=this.hover?'pointer':this.mode==='pan'?'move':'grab';
      if (this.hover) {
        const {galaxy:g,node:n}=this.hover;
        // Build tooltip text with DOM text nodes, including untrusted titles.
        this.tooltip.replaceChildren(document.createTextNode(n?n.label:g.label));
        const small=document.createElement('small'); small.textContent=n?`${g.label} · ${n.scored === false ? '未评分' : '相关度 ' + Math.round(n.score)} · 点击阅读`:`${g.count} 条主归属 · 点击进入 · 拖拽旋转`;
        this.tooltip.append(small); this.tooltip.hidden=false;
        this.tooltip.style.left=this.clamp(p.x+14,10,Math.max(10,this.width-this.tooltip.offsetWidth-10))+'px';
        this.tooltip.style.top=this.clamp(p.y+14,10,this.height-this.tooltip.offsetHeight-10)+'px';
      } else this.tooltip.hidden=true;
      this.draw();
    });
    canvas.addEventListener('pointerup',e=> {
      if (!this.drag||e.pointerId!==this.drag.pointer) return;
      const moved=this.drag.moved; this.drag=null; canvas.releasePointerCapture(e.pointerId);
      if (moved||e.button!==0) return;
      const p=local(e),hit=this.hit(p.x,p.y); if (hit) this.onPick(hit);
    });
    canvas.addEventListener('pointercancel',()=>{this.drag=null;});
    canvas.addEventListener('lostpointercapture',()=>{this.drag=null;});
    canvas.addEventListener('pointerleave',()=>{if (!this.drag) {this.hover=null;this.tooltip.hidden=true;this.draw();}});
    canvas.addEventListener('wheel',e=> {
      e.preventDefault(); this.setAutoOrbit(false);
      this.zoom(Math.exp(-this.clamp(e.deltaY*(e.deltaMode===1?16:1),-120,120)*.0025),local(e));
    },{passive:false});
  }
}
