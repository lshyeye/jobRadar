import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import type { GalaxyHoverInfo, Job, MatchScore } from '../types';

/* 类型 → 星团颜色（RGB 0-1）与配置 */
const OTHER_STAR = { color: [0.42, 0.47, 0.58] as [number, number, number], center: [0, 26, -10] as [number, number, number], size: 1.4 };

const TYPE_STAR: Record<
  string,
  { color: [number, number, number]; center: [number, number, number]; size: number }
> = {
  Agent应用开发: { color: [1.0, 0.66, 0.16], center: [42, 8, 0], size: 2.6 },
  前端: { color: [0.25, 0.5, 1.0], center: [-42, 6, 14], size: 2.4 },
  全栈: { color: [0.1, 0.78, 0.55], center: [0, -12, -44], size: 2.3 },
  其他: OTHER_STAR,
};

const VERTEX_SHADER = /* glsl */ `
  attribute vec3 aColor;
  attribute float aSize;
  varying vec3 vColor;
  void main() {
    vColor = aColor;
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    gl_PointSize = aSize * (260.0 / -mv.z);
    gl_Position = projectionMatrix * mv;
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  varying vec3 vColor;
  void main() {
    vec2 uv = gl_PointCoord - 0.5;
    float d = length(uv);
    float alpha = smoothstep(0.5, 0.05, d);
    if (alpha < 0.01) discard;
    // 中心亮核 + 柔和光晕
    float core = smoothstep(0.18, 0.0, d) * 0.6;
    gl_FragColor = vec4(vColor * (1.0 + core * 2.0), alpha);
  }
`;

interface Props {
  jobs: Job[];
  scores: Map<string, MatchScore>;
  onSelect: (job: Job) => void;
}

/**
 * 岗位星系：每条 JD 一个发光粒子，按职位类型聚成星团。
 * 自动旋转 + 拖拽（带惯性）+ hover 悬浮卡 + 点击选中岗位。
 */
export function JobGalaxy({ jobs, scores, onSelect }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<GalaxyHoverInfo | null>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host || jobs.length === 0) return;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(58, host.clientWidth / host.clientHeight, 0.1, 1200);
    camera.position.set(0, 10, 150);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(host.clientWidth, host.clientHeight);
    host.appendChild(renderer.domElement);

    /* ---------- 岗位粒子 ---------- */
    const n = jobs.length;
    const positions = new Float32Array(n * 3);
    const colors = new Float32Array(n * 3);
    const sizes = new Float32Array(n);
    const gauss = () =>
      (Math.random() + Math.random() + Math.random() - 1.5) * 16; // 近高斯散布

    jobs.forEach((job, i) => {
      const conf = TYPE_STAR[job.type] ?? OTHER_STAR;
      let size = conf.size;
      if (scores.has(job.url)) size *= 1.5; // 已做 LLM 分析的岗位更亮
      positions[i * 3] = conf.center[0] + gauss() * 1.4;
      positions[i * 3 + 1] = conf.center[1] + gauss();
      positions[i * 3 + 2] = conf.center[2] + gauss();
      colors[i * 3] = conf.color[0];
      colors[i * 3 + 1] = conf.color[1];
      colors[i * 3 + 2] = conf.color[2];
      sizes[i] = size;
    });

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('aColor', new THREE.BufferAttribute(colors, 3));
    geo.setAttribute('aSize', new THREE.BufferAttribute(sizes, 1));
    const mat = new THREE.ShaderMaterial({
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const points = new THREE.Points(geo, mat);
    const group = new THREE.Group();
    group.add(points);
    scene.add(group);

    /* ---------- 背景微星 ---------- */
    const starN = 420;
    const starPos = new Float32Array(starN * 3);
    const starCol = new Float32Array(starN * 3);
    const starSize = new Float32Array(starN);
    for (let i = 0; i < starN; i++) {
      const r = 190 + Math.random() * 190;
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      starPos[i * 3] = r * Math.sin(phi) * Math.cos(theta);
      starPos[i * 3 + 1] = r * Math.cos(phi) * 0.55;
      starPos[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta);
      const b = 0.35 + Math.random() * 0.4;
      starCol[i * 3] = b;
      starCol[i * 3 + 1] = b;
      starCol[i * 3 + 2] = b * 1.1;
      starSize[i] = 0.7 + Math.random() * 1.1;
    }
    const starGeo = new THREE.BufferGeometry();
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3));
    starGeo.setAttribute('aColor', new THREE.BufferAttribute(starCol, 3));
    starGeo.setAttribute('aSize', new THREE.BufferAttribute(starSize, 1));
    const stars = new THREE.Points(starGeo, mat);
    scene.add(stars);

    /* ---------- 交互 ---------- */
    const raycaster = new THREE.Raycaster();
    raycaster.params.Points = { threshold: 3 };
    const pointer = new THREE.Vector2();
    let dragX = -1;
    let dragY = -1;
    let velY = 0.0011; // 自动旋转基速
    let velX = 0;
    let dragging = false;
    let hoverIdx = -1;

    const pick = (ev: { clientX: number; clientY: number }): number => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObject(points, false);
      const first = hits[0];
      return first?.index ?? -1;
    };

    const onPointerDown = (ev: PointerEvent) => {
      dragging = true;
      dragX = ev.clientX;
      dragY = ev.clientY;
    };
    const onPointerMove = (ev: PointerEvent) => {
      if (dragging && dragX >= 0) {
        const dx = ev.clientX - dragX;
        const dy = ev.clientY - dragY;
        velY = dx * 0.00022 + velY * 0.5;
        velX = dy * 0.00016 + velX * 0.5;
      }
      dragX = ev.clientX;
      dragY = ev.clientY;
      const idx = ev.buttons === 0 ? pick(ev) : -1;
      const rect = renderer.domElement.getBoundingClientRect();
      const hoveredJob = idx >= 0 ? jobs[idx] : undefined;
      if (hoveredJob) {
        setHover({
          job: hoveredJob,
          x: ev.clientX - rect.left,
          y: ev.clientY - rect.top,
        });
        renderer.domElement.style.cursor = 'pointer';
      } else {
        if (hoverIdx !== -1) setHover(null);
        renderer.domElement.style.cursor = dragging ? 'grabbing' : 'grab';
      }
      hoverIdx = idx;
    };
    const onPointerUp = () => {
      dragging = false;
    };
    const onClick = (ev: MouseEvent) => {
      const idx = pick(ev);
      const job = idx >= 0 ? jobs[idx] : undefined;
      if (job) onSelect(job);
    };

    renderer.domElement.addEventListener('pointerdown', onPointerDown);
    renderer.domElement.addEventListener('pointermove', onPointerMove);
    renderer.domElement.addEventListener('pointerup', onPointerUp);
    renderer.domElement.addEventListener('click', onClick);
    renderer.domElement.style.cursor = 'grab';

    /* ---------- 渲染循环 ---------- */
    let raf = 0;
    const clock = new THREE.Clock();
    const tick = () => {
      const t = clock.getElapsedTime();
      group.rotation.y += velY;
      group.rotation.x = THREE.MathUtils.clamp(
        group.rotation.x + velX,
        -0.5,
        0.5,
      );
      // 惯性衰减 → 回归自动旋转
      velY += (0.0011 - velY) * 0.01;
      velX *= 0.92;
      group.position.y = Math.sin(t * 0.5) * 2.2; // 整体缓慢浮动
      stars.rotation.y -= 0.00025;
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    };
    tick();

    /* ---------- resize ---------- */
    const ro = new ResizeObserver(() => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
    ro.observe(host);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener('pointerdown', onPointerDown);
      renderer.domElement.removeEventListener('pointermove', onPointerMove);
      renderer.domElement.removeEventListener('pointerup', onPointerUp);
      renderer.domElement.removeEventListener('click', onClick);
      geo.dispose();
      starGeo.dispose();
      mat.dispose();
      renderer.dispose();
      if (renderer.domElement.parentElement === host) host.removeChild(renderer.domElement);
    };
  }, [jobs, scores, onSelect]);

  const counts = jobs.reduce<Record<string, number>>((acc, j) => {
    acc[j.type] = (acc[j.type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="galaxy-wrap">
      <div className="galaxy-canvas" ref={hostRef} />
      <div className="galaxy-caption">
        <h2>岗位星系</h2>
        <p>{jobs.length} 条 JD · 拖拽旋转 · 点击粒子查看岗位 · 大颗粒 = 已 AI 分析</p>
        <div className="galaxy-legend">
          {(['Agent应用开发', '前端', '全栈'] as const).map((t) => (
            <span key={t} className={`gl gl-${t === '前端' ? 'front' : t === '全栈' ? 'full' : 'agent'}`}>
              {t} {counts[t] ?? 0}
            </span>
          ))}
        </div>
      </div>
      {hover && (
        <div
          className="galaxy-tip"
          style={{ left: hover.x + 14, top: hover.y + 14 }}
        >
          <div className="t">{hover.job.title}</div>
          <div className="s">
            {hover.job.company} · {hover.job.city || '—'} · {hover.job.type}
          </div>
        </div>
      )}
    </div>
  );
}
