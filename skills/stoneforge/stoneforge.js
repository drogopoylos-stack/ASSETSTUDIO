/**
 * ============================================================================
 *  StoneForge — procedural modular hard-surface toolkit for three.js
 * ============================================================================
 *
 *  A tiny, dependency-light library for building weathered, realistic,
 *  TEXTURELESS hard-surface assets (stone, brick, concrete, rock, wood, metal)
 *  entirely in code. Extracted from the "mossy archway" study.
 *
 *  THE FOUR IDEAS
 *  --------------
 *   1. Modular kit  — assets are many small primitive pieces (beveled boxes,
 *      extruded wedges) placed parametrically with a little random "jitter".
 *   2. One mesh     — every piece is baked to world space and MERGED into a
 *      single BufferGeometry => one draw call for the whole asset.
 *   3. Per-piece attributes — before merging, each piece is tagged with vertex
 *      attributes carrying its OWN local position/normal, half-size and a random
 *      seed. That lets a single shared shader still treat every block
 *      individually (edge wear, which-face-is-up, unique noise) even though it
 *      is now one merged mesh. This is what makes #2 possible without losing
 *      per-block control.
 *   4. UV-free procedural PBR — a customized MeshStandardMaterial samples 3D
 *      world-space noise for colour/relief (seamless on every face, no UV
 *      unwrap, no texture files) and adds edge wear + a derivative-based bump.
 *      An optional "overgrowth" layer paints moss / snow / rust / sand onto the
 *      up-facing surfaces and drips it down the edges — all in the shader,
 *      ZERO extra triangles.
 *
 *  USAGE (minimal)
 *  ---------------
 *    import { StoneKit, beveledBox, addArch, makeStoneMaterial, OVERGROWTH }
 *      from './stoneforge.js';
 *
 *    const kit = new StoneKit();
 *    // a stacked column
 *    for (let i=0;i<6;i++)
 *      kit.add(beveledBox(0.8,0.5,0.8), { position:new THREE.Vector3(0,0.25+i*0.5,0) });
 *    const geo = kit.build();
 *
 *    const { material, uniforms } = makeStoneMaterial({
 *      colorA:0x3a3a3c, colorB:0x8f8a80,
 *      overgrowth: OVERGROWTH.moss,
 *    });
 *    scene.add(new THREE.Mesh(geo, material));
 *
 *  Requires three r150+ (needs mergeGeometries, RoundedBoxGeometry).
 * ============================================================================
 */

import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

/* -------------------------------------------------------------------------- */
/*  GLSL: simplex noise + fbm (exported for advanced/custom shaders)          */
/* -------------------------------------------------------------------------- */
export const SF_NOISE_GLSL = /* glsl */`
vec3 sf_mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 sf_mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 sf_permute(vec4 x){return sf_mod289(((x*34.0)+1.0)*x);}
vec4 sf_taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy; i=sf_mod289(i);
  vec4 p=sf_permute(sf_permute(sf_permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=sf_taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
float fbm(vec3 p,int oct){ float s=0.0,a=0.5,f=1.0; for(int i=0;i<8;i++){ if(i>=oct)break; s+=a*snoise(p*f); f*=2.0; a*=0.5; } return s; }
`;

/* -------------------------------------------------------------------------- */
/*  Small helpers                                                             */
/* -------------------------------------------------------------------------- */
const _ZERO = new THREE.Vector3(0, 0, 0);
const _ONE  = new THREE.Vector3(1, 1, 1);
const _IQ   = new THREE.Quaternion();
const _Z    = new THREE.Vector3(0, 0, 1);

function col(x){ return x instanceof THREE.Color ? x.clone() : new THREE.Color(x); }

function _keepOnly(geo, keep){
  for(const name of Object.keys(geo.attributes)) if(!keep.includes(name)) geo.deleteAttribute(name);
  geo.morphAttributes = {};
}
function _fill(geo, name, vals, item){
  const n = geo.attributes.position.count;
  const arr = new Float32Array(n*item);
  for(let i=0;i<n;i++) for(let k=0;k<item;k++) arr[i*item+k]=vals[k];
  geo.setAttribute(name, new THREE.BufferAttribute(arr, item));
}
function _bboxSize(geo){
  geo.computeBoundingBox();
  return new THREE.Vector3().subVectors(geo.boundingBox.max, geo.boundingBox.min);
}

/* -------------------------------------------------------------------------- */
/*  StoneKit — accumulate tagged pieces, merge to one geometry                */
/* -------------------------------------------------------------------------- */
export class StoneKit {
  /**
   * @param {object} [opts]
   * @param {object} [opts.extras]  extra per-piece attributes as {name:itemSize},
   *   e.g. {aMask:1}. Pass their values via add(..., {attrs:{aMask:1}}).
   */
  constructor(opts = {}){
    this.extras = opts.extras || {};
    this._geos = [];
    this._m = new THREE.Matrix4();
  }

  /**
   * Tag a CENTERED local-space geometry and bake it into world space.
   * @param {THREE.BufferGeometry} geo  centered geometry (position+normal). Consumed/mutated.
   * @param {object} [t]
   * @param {THREE.Vector3}   [t.position]
   * @param {THREE.Quaternion}[t.quaternion]
   * @param {THREE.Vector3}   [t.scale]
   * @param {THREE.Vector3}   [t.size]   full size for edge-wear normalisation (defaults to bbox)
   * @param {number}          [t.seed]   per-piece random seed (defaults random)
   * @param {object}          [t.attrs]  values for any extras declared in the ctor
   */
  add(geo, t = {}){
    _keepOnly(geo, ['position','normal']);
    // capture LOCAL pos/normal BEFORE the world transform is baked in
    geo.setAttribute('aLocalPos',    geo.attributes.position.clone());
    geo.setAttribute('aLocalNormal', geo.attributes.normal.clone());
    const size = t.size || _bboxSize(geo);
    _fill(geo, 'aHalf', [size.x/2, size.y/2, size.z/2], 3);
    _fill(geo, 'aSeed', [t.seed ?? Math.random()*100], 1);
    for(const name in this.extras){
      const item = this.extras[name];
      let v = (t.attrs && name in t.attrs) ? t.attrs[name] : (item===1 ? 0 : new Array(item).fill(0));
      _fill(geo, name, Array.isArray(v) ? v : [v], item);
    }
    // bake world transform (only position/normal are transformed; the aLocal* copies stay local)
    this._m.compose(t.position || _ZERO, t.quaternion || _IQ, t.scale || _ONE);
    geo.applyMatrix4(this._m);
    this._geos.push(geo);
    return this;
  }

  /** @returns {THREE.BufferGeometry} single merged geometry (one draw call) */
  build(){
    if(!this._geos.length) return new THREE.BufferGeometry();
    const merged = mergeGeometries(this._geos, false);
    this._geos.length = 0;
    return merged;
  }
}

/* -------------------------------------------------------------------------- */
/*  Primitive builders (return CENTERED geometries)                           */
/* -------------------------------------------------------------------------- */

/** Beveled box — the workhorse block (pillar drums, bricks, steps, tiles). */
export function beveledBox(w, h, d, { bevel = 0.04, seg = 2 } = {}){
  const r = Math.min(bevel, Math.min(w, h, d) * 0.45);
  return new RoundedBoxGeometry(w, h, d, seg, r);
}

/** Beveled trapezoidal prism — an arch voussoir / tapered wedge stone. */
export function voussoir(innerW, outerW, thick, depth, { bevel = 0.04 } = {}){
  const s = new THREE.Shape();
  s.moveTo(-innerW/2, -thick/2); s.lineTo(innerW/2, -thick/2);
  s.lineTo(outerW/2,  thick/2);  s.lineTo(-outerW/2, thick/2); s.closePath();
  const g = new THREE.ExtrudeGeometry(s, {
    depth, bevelEnabled:true, bevelThickness:bevel, bevelSize:bevel, bevelSegments:1, steps:1,
  });
  g.translate(0, 0, -depth/2);
  g.computeVertexNormals();
  return g;
}

/* -------------------------------------------------------------------------- */
/*  Layout convenience helpers                                                */
/* -------------------------------------------------------------------------- */

/**
 * Add a semicircular arch of voussoirs to a kit.
 * @param {StoneKit} kit
 * @param {object} o { count, innerR, thick, depth, center(Vector3), bevel, jitter }
 */
export function addArch(kit, o = {}){
  const { count=13, innerR=1, thick=0.7, depth=0.9,
          center=new THREE.Vector3(), bevel=0.04, jitter=0.02 } = o;
  const outerR = innerR + thick, rMid = (innerR + outerR)/2;
  for(let i=0;i<count;i++){
    const phi = (i+0.5)/count*Math.PI, dphi = Math.PI/count;
    const wIn = innerR*dphi*0.9, wOut = outerR*dphi*0.9;
    const g = voussoir(wIn, wOut, thick, depth, { bevel });
    const q = new THREE.Quaternion().setFromAxisAngle(_Z, phi - Math.PI/2);
    if(jitter) q.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(
      (Math.random()-0.5)*jitter, (Math.random()-0.5)*jitter, (Math.random()-0.5)*jitter)));
    const pos = new THREE.Vector3(Math.cos(phi)*rMid, Math.sin(phi)*rMid, 0).add(center);
    kit.add(g, { position:pos, quaternion:q, size:new THREE.Vector3(wOut, thick, depth) });
  }
  return kit;
}

/**
 * Add a vertical stack of blocks (column drums, wall courses).
 * @param {StoneKit} kit
 * @param {object} o { base(Vector3), block(w,h,d), count, gap, jitter, taper, engraveNone }
 */
export function addStack(kit, o = {}){
  const { base=new THREE.Vector3(), w=0.8, h=0.5, d=0.8, count=5,
          gap=0.0, jitter=0.02, taper=0 } = o;
  let y = base.y;
  for(let i=0;i<count;i++){
    const t = count>1 ? i/(count-1) : 0;
    const bw = w*(1 - taper*t), bd = d*(1 - taper*t);
    const g = beveledBox(bw, h, bd);
    const q = jitter ? new THREE.Quaternion().setFromEuler(new THREE.Euler(
      (Math.random()-0.5)*jitter, (Math.random()-0.5)*jitter*1.6, (Math.random()-0.5)*jitter)) : _IQ;
    const p = new THREE.Vector3(base.x + (Math.random()-0.5)*jitter*0.5, y + h/2, base.z);
    kit.add(g, { position:p, quaternion:q, size:new THREE.Vector3(bw,h,bd) });
    y += h + gap;
  }
  return kit;
}

/**
 * Add a rectangular brick wall (running bond) in the XY plane.
 * @param {StoneKit} kit
 * @param {object} o { origin(Vector3), cols, rows, brick(w,h,d), gap, jitter }
 */
export function addBrickWall(kit, o = {}){
  const { origin=new THREE.Vector3(), cols=8, rows=6,
          w=0.5, h=0.28, d=0.34, gap=0.02, jitter=0.015 } = o;
  for(let r=0;r<rows;r++){
    const off = (r%2) ? (w+gap)*0.5 : 0;         // running bond
    for(let c=0;c<cols;c++){
      const g = beveledBox(w, h, d, { bevel:0.02 });
      const q = new THREE.Quaternion().setFromEuler(new THREE.Euler(
        (Math.random()-0.5)*jitter, (Math.random()-0.5)*jitter, (Math.random()-0.5)*jitter));
      const p = new THREE.Vector3(
        origin.x + off + c*(w+gap) + (Math.random()-0.5)*0.01,
        origin.y + h/2 + r*(h+gap),
        origin.z + (Math.random()-0.5)*0.02);
      kit.add(g, { position:p, quaternion:q, size:new THREE.Vector3(w,h,d) });
    }
  }
  return kit;
}

/* -------------------------------------------------------------------------- */
/*  Overgrowth presets — "stuff that accumulates on top and drips down edges" */
/* -------------------------------------------------------------------------- */
export const OVERGROWTH = {
  //            dark        mid         lite        up    cover drip  scale amount fuzz
  moss:  { dark:0x0a1c05, mid:0x16330a, lite:0x35440c, up:0.18, cover:1.0, drip:1.1, scale:1.0, amount:1.0, fuzz:13.0 },
  snow:  { dark:0x9fb0c0, mid:0xd8e2ee, lite:0xffffff, up:0.45, cover:1.2, drip:0.4, scale:0.7, amount:1.0, fuzz:8.0  },
  rust:  { dark:0x2a1206, mid:0x6e3410, lite:0xa5541f, up:0.05, cover:0.8, drip:1.6, scale:1.2, amount:0.9, fuzz:18.0 },
  sand:  { dark:0x6b5a34, mid:0x9c8552, lite:0xc7b782, up:0.30, cover:0.9, drip:0.6, scale:0.9, amount:0.85, fuzz:10.0 },
  lichen:{ dark:0x394a2a, mid:0x6d7d4a, lite:0xb7bd7e, up:0.10, cover:0.7, drip:0.5, scale:1.6, amount:0.8, fuzz:22.0 },
};

/* -------------------------------------------------------------------------- */
/*  makeStoneMaterial — the procedural, UV-free PBR stone material            */
/* -------------------------------------------------------------------------- */
/**
 * @param {object} [o]
 * @param {number|THREE.Color} [o.colorA=0x2a2a2c]  darker base stone
 * @param {number|THREE.Color} [o.colorB=0x857f74]  lighter base stone
 * @param {number} [o.roughness=0.9]
 * @param {number} [o.noiseScale=0.55]  base colour-variation frequency (bigger = smaller blotches)
 * @param {number} [o.edgeWear=0.6]     0..1 how much chipped/worn-light the edges get
 * @param {number} [o.bump=0.03]        surface relief strength (world units)
 * @param {number} [o.damp=0.3]         darken toward the base (y=0), wet look
 * @param {boolean}[o.warm=true]        warm mineral veins
 * @param {number} [o.envMapIntensity=0.75]
 * @param {object|null} [o.overgrowth=null]  an OVERGROWTH preset or custom {dark,mid,lite,up,cover,drip,scale,amount,fuzz}
 * @returns {{material:THREE.MeshStandardMaterial, uniforms:object}}
 */
export function makeStoneMaterial(o = {}){
  const P = Object.assign({
    colorA:0x2a2a2c, colorB:0x857f74, roughness:0.9, noiseScale:0.55,
    edgeWear:0.6, bump:0.03, damp:0.3, warm:true, envMapIntensity:0.75, overgrowth:null,
  }, o);

  const og = P.overgrowth;
  const uniforms = {
    uTime:      { value:0 },
    uColorA:    { value: col(P.colorA) },
    uColorB:    { value: col(P.colorB) },
    uNoiseScale:{ value: P.noiseScale },
    uEdgeWear:  { value: P.edgeWear },
    uBump:      { value: P.bump },
    uDamp:      { value: P.damp },
    // overgrowth (safe defaults even when disabled)
    uOgDark:    { value: col(og ? og.dark : 0x0a1c05) },
    uOgMid:     { value: col(og ? og.mid  : 0x16330a) },
    uOgLite:    { value: col(og ? og.lite : 0x35440c) },
    uOgUp:      { value: og ? og.up     : 0.2 },
    uOgCover:   { value: og ? og.cover  : 1.0 },
    uOgDrip:    { value: og ? og.drip   : 1.0 },
    uOgScale:   { value: og ? og.scale  : 1.0 },
    uOgAmount:  { value: og ? (og.amount ?? 1.0) : 0.0 },
    uOgFuzz:    { value: og ? (og.fuzz  ?? 13.0) : 13.0 },
  };

  const mat = new THREE.MeshStandardMaterial({ color:0xffffff, roughness:P.roughness, metalness:0.0 });
  mat.envMapIntensity = P.envMapIntensity;
  mat.defines = { SF_WARM: P.warm ? 1 : 0, SF_OVERGROWTH: og ? 1 : 0 };

  mat.onBeforeCompile = (sh) => {
    Object.assign(sh.uniforms, uniforms);

    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', /* glsl */`#include <common>
        attribute vec3 aLocalPos; attribute vec3 aLocalNormal; attribute vec3 aHalf; attribute float aSeed;
        varying vec3 vWPos; varying vec3 vWN; varying vec3 vLPos; varying vec3 vLN; varying vec3 vHalf; varying float vSeed;`)
      .replace('#include <beginnormal_vertex>', `#include <beginnormal_vertex>
        vLN = aLocalNormal;`)
      .replace('#include <project_vertex>', `#include <project_vertex>
        vLPos=aLocalPos; vHalf=aHalf; vSeed=aSeed;
        vWPos=(modelMatrix*vec4(transformed,1.0)).xyz;
        vWN=normalize(mat3(modelMatrix)*objectNormal);`);

    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', /* glsl */`#include <common>
        ${SF_NOISE_GLSL}
        varying vec3 vWPos; varying vec3 vWN; varying vec3 vLPos; varying vec3 vLN; varying vec3 vHalf; varying float vSeed;
        uniform float uTime, uNoiseScale, uEdgeWear, uBump, uDamp;
        uniform vec3 uColorA, uColorB;
        uniform vec3 uOgDark, uOgMid, uOgLite;
        uniform float uOgUp, uOgCover, uOgDrip, uOgScale, uOgAmount, uOgFuzz;
        vec3 gWN; float gRough;`)

      .replace('#include <map_fragment>', /* glsl */`
        vec3 wpos=vWPos; vec3 N=normalize(vWN); float seed=vSeed;

        // distance to this block's faces -> edge/corner closeness
        vec3 edged=max(vHalf-abs(vLPos),0.0);
        float e1=min(edged.x,min(edged.y,edged.z));
        float e3=max(edged.x,max(edged.y,edged.z));
        float e2=edged.x+edged.y+edged.z-e1-e3;
        float hasAttr=step(0.0001, vHalf.x+vHalf.y+vHalf.z);
        float edgeF=smoothstep(0.06,0.0,e2)*hasAttr;

        // ---- base stone colour (3D noise, no UVs) ----
        float big=fbm(wpos*uNoiseScale+seed*7.31,3);
        float med=fbm(wpos*(uNoiseScale*3.8)+11.0,3);
        float fine=snoise(wpos*9.0+seed);
        vec3 stone=mix(uColorA,uColorB, smoothstep(-0.55,0.6,big));
        stone*=0.9+0.16*med;
        #if SF_WARM
          stone=mix(stone, stone*vec3(1.06,1.0,0.9), smoothstep(0.0,0.7, fbm(wpos*0.32+21.0,2)));
        #endif
        float stain=smoothstep(0.30,0.85, fbm(wpos*1.25+50.0,3));
        stone*=mix(1.0,0.62,stain*0.55);
        stone*=0.93+0.14*fine;
        stone*=mix(1.0,0.82, smoothstep(1.7,0.0,wpos.y)*uDamp);   // damp base
        stone=mix(stone, stone*1.28+0.03, edgeF*uEdgeWear);       // worn light edges

        vec3 albedo=stone;
        float rough=0.72+0.16*fine;
        float H = (fbm(wpos*7.0,3)*0.5 + snoise(wpos*19.0)*0.16) * uBump;

        // ---- optional OVERGROWTH layer (moss / snow / rust / sand ...) ----
        #if SF_OVERGROWTH
          float upf=clamp(N.y,0.0,1.0);
          float mn=fbm(wpos*1.4*uOgScale+seed*3.0,4)*0.5+0.5;
          float top=smoothstep(uOgUp,uOgUp+0.42,upf)*smoothstep(0.30,0.72, mn+upf*0.30);
          float ty=clamp(vLPos.y/max(vHalf.y,1e-3)*0.5+0.5,0.0,1.0);
          float streak=fbm(vec3(wpos.x*3.6,wpos.y*0.9,wpos.z*3.6)*uOgScale+9.0,4)*0.5+0.5;
          float drip=(1.0-upf)*smoothstep(0.26,1.0,ty)*smoothstep(0.42,0.86,streak)*uOgDrip;
          float ov=clamp((top+drip)*uOgCover,0.0,1.0);
          ov*=(1.0-edgeF*0.35);
          ov=smoothstep(0.10,0.55,ov)*uOgAmount;

          float f1=fbm(wpos*uOgFuzz+7.0,4)*0.5+0.5;      // clumpy grain
          float f2=fbm(wpos*(uOgFuzz*2.5),3)*0.5+0.5;    // fine fuzz
          float tex=clamp(f1*0.68+f2*0.32,0.0,1.0);

          float mc=fbm(wpos*2.6*uOgScale+3.0,3)*0.5+0.5;
          vec3 oc=mix(uOgDark,uOgMid, smoothstep(0.2,0.6,mc));
          oc=mix(oc,uOgLite, smoothstep(0.5,0.95,mc)*mix(0.45,1.0,upf));
          oc*=mix(0.50,1.35,tex);       // bright grain tips / dark valleys = fake volume
          oc*=mix(0.72,1.0,ty);

          albedo=mix(albedo, oc, ov);
          rough =mix(rough, 0.95, ov);
          H     += (tex-0.5)*ov*uBump*3.5;   // fuzzy relief where overgrown
        #endif

        diffuseColor.rgb=albedo;
        gRough=clamp(rough,0.3,1.0);

        // ---- derivative bump (Mikkelsen) -> shading normal, no normal map ----
        vec3 dpx=dFdx(wpos), dpy=dFdy(wpos);
        float dHx=dFdx(H), dHy=dFdy(H);
        vec3 r1=cross(dpy,N), r2=cross(N,dpx);
        float det=dot(dpx,r1);
        gWN=normalize(abs(det)*N - sign(det)*(dHx*r1+dHy*r2));
      `)

      .replace('#include <roughnessmap_fragment>', `float roughnessFactor=gRough;`)
      .replace('#include <normal_fragment_begin>', `#include <normal_fragment_begin>
        normal=normalize((viewMatrix*vec4(gWN,0.0)).xyz);`);

    mat.userData.shader = sh;
  };

  return { material: mat, uniforms };
}

/* -------------------------------------------------------------------------- */
/*  Optional: a decent studio setup so you can preview assets fast.           */
/*  (Purely convenience — safe to ignore in your own scene.)                  */
/* -------------------------------------------------------------------------- */
export function quickStudio(renderer, scene){
  const key=new THREE.DirectionalLight(0xfff4e6,2.0); key.position.set(-3.2,8.6,7.6);
  key.castShadow=true; key.shadow.mapSize.set(2048,2048); key.shadow.radius=8;
  key.shadow.bias=-0.0004; key.shadow.normalBias=0.02;
  const sc=key.shadow.camera; sc.near=1; sc.far=40; sc.left=-10; sc.right=10; sc.top=12; sc.bottom=-4;
  const fill=new THREE.DirectionalLight(0xe6eeff,0.9); fill.position.set(6,4,3);
  const hemi=new THREE.HemisphereLight(0xdde3dd,0x3a3026,0.85);
  const amb=new THREE.AmbientLight(0xbcc1bb,0.4);
  scene.add(key,fill,hemi,amb);
  renderer.shadowMap.enabled=true; renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  renderer.toneMapping=THREE.NeutralToneMapping ?? THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure=1.02;
  return { key, fill, hemi, amb };
}
