import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'

// GPU 线程模型 3D 场景：把 CUDA 的 Grid → Block → Thread 层级画成立方体阵列。
//  - Grid：gx×gy 个 Block 铺在 XZ 平面，块间留缝；
//  - Block：每个 Block 内是 bx×by 个 thread 小立方体；
//  - 全部 thread 用一个 InstancedMesh 画（这里 16 块 × 64 线程 = 1024 个，可扩到上万）。
// 三种着色模式演示不同概念：
//  - blocks：按 Block 上色，看清网格如何被划分成块；
//  - warps：线程按 warp(32) 分组，逐 warp 高亮扫过，演示「warp 是调度单位」；
//  - thread：高亮单个线程扫过，演示最细粒度。
//
// 返回 { setMode, dispose }。后续新主题（tiling/cache/...）可在此扩展更多 build* 场景。
export function createGpuScene(canvas, container) {
  const cfg = { gx: 4, gy: 4, bx: 8, by: 8, warpSize: 32 }
  const step = 0.78
  const blockGap = 1.7
  const cubeSize = 0.6
  const perBlock = cfg.bx * cfg.by
  const blockCount = cfg.gx * cfg.gy
  const total = blockCount * perBlock
  const warpsPerBlock = Math.ceil(perBlock / cfg.warpSize)

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

  const scene = new THREE.Scene()
  scene.background = new THREE.Color('#0b0e14')

  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 1000)
  camera.position.set(20, 17, 24)

  scene.add(new THREE.AmbientLight(0xffffff, 0.75))
  const dir = new THREE.DirectionalLight(0xffffff, 0.95)
  dir.position.set(12, 22, 10)
  scene.add(dir)

  const controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.maxPolarAngle = Math.PI * 0.49

  // ---- 实例化线程立方体 ----
  const geo = new THREE.BoxGeometry(cubeSize, cubeSize, cubeSize)
  const mat = new THREE.MeshLambertMaterial()
  const mesh = new THREE.InstancedMesh(geo, mat, total)
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage)

  const blockW = cfg.bx * step
  const blockD = cfg.by * step
  const gridW = cfg.gx * blockW + (cfg.gx - 1) * blockGap
  const gridD = cfg.gy * blockD + (cfg.gy - 1) * blockGap

  const meta = new Array(total)
  const dummy = new THREE.Object3D()
  let idx = 0
  for (let byi = 0; byi < cfg.gy; byi++) {
    for (let bxi = 0; bxi < cfg.gx; bxi++) {
      const blockId = byi * cfg.gx + bxi
      const ox = -gridW / 2 + bxi * (blockW + blockGap)
      const oz = -gridD / 2 + byi * (blockD + blockGap)
      for (let ty = 0; ty < cfg.by; ty++) {
        for (let tx = 0; tx < cfg.bx; tx++) {
          const linear = ty * cfg.bx + tx
          dummy.position.set(ox + tx * step + step / 2, 0, oz + ty * step + step / 2)
          dummy.updateMatrix()
          mesh.setMatrixAt(idx, dummy.matrix)
          meta[idx] = { blockId, linear, warpId: Math.floor(linear / cfg.warpSize) }
          idx++
        }
      }
    }
  }
  mesh.instanceMatrix.needsUpdate = true
  scene.add(mesh)

  // 地面参考网格
  const grid = new THREE.GridHelper(Math.max(gridW, gridD) + 6, 24, 0x1d2740, 0x141a2b)
  grid.position.y = -cubeSize
  scene.add(grid)

  // ---- 着色 ----
  const color = new THREE.Color()
  let mode = 'blocks'
  let activeBlock = 0
  let activeWarp = 0
  let activeThread = 0

  function recolor() {
    for (let k = 0; k < total; k++) {
      const m = meta[k]
      if (mode === 'blocks') {
        color.setHSL(((m.blockId * 47) % 360) / 360, 0.6, 0.55)
      } else if (mode === 'warps') {
        if (m.blockId === activeBlock && m.warpId === activeWarp) color.setHSL(0.24, 0.85, 0.55)
        else color.setHSL(0.6, 0.15, 0.18)
      } else {
        if (m.blockId === activeBlock && m.linear === activeThread) color.setHSL(0.1, 0.9, 0.6)
        else color.setHSL(0.6, 0.12, 0.16)
      }
      mesh.setColorAt(k, color)
    }
    mesh.instanceColor.needsUpdate = true
  }
  recolor()

  function advance() {
    if (mode === 'warps') {
      activeWarp++
      if (activeWarp >= warpsPerBlock) {
        activeWarp = 0
        activeBlock = (activeBlock + 1) % blockCount
      }
    } else if (mode === 'thread') {
      activeThread += cfg.warpSize // 跳着走，演示更快扫完
      if (activeThread >= perBlock) {
        activeThread = activeThread % cfg.warpSize === 0 ? (activeThread % perBlock) : 0
        if (activeThread === 0) activeBlock = (activeBlock + 1) % blockCount
      }
    }
    recolor()
  }

  // ---- 循环 / 自适应 ----
  const clock = new THREE.Clock()
  let acc = 0
  let raf = 0
  function loop() {
    raf = requestAnimationFrame(loop)
    const dt = clock.getDelta()
    controls.update()
    if (mode !== 'blocks') {
      acc += dt
      if (acc >= 0.7) {
        acc = 0
        advance()
      }
    }
    renderer.render(scene, camera)
  }

  function resize() {
    const w = container.clientWidth || 1
    const h = container.clientHeight || 1
    renderer.setSize(w, h, false)
    camera.aspect = w / h
    camera.updateProjectionMatrix()
  }
  const ro = new ResizeObserver(resize)
  ro.observe(container)
  resize()
  loop()

  return {
    setMode(m) {
      mode = m
      activeBlock = 0
      activeWarp = 0
      activeThread = 0
      acc = 0
      recolor()
    },
    dispose() {
      cancelAnimationFrame(raf)
      ro.disconnect()
      controls.dispose()
      geo.dispose()
      mat.dispose()
      renderer.dispose()
    },
  }
}
