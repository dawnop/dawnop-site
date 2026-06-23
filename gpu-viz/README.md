# gpu-viz

用交互式 3D 模型讲解 GPU 概念（线程模型 / 分块 / 流水线 / 缓存 / PTX）的**独立**前端工程，
与博客 `frontend/` 完全解耦，构建后部署在 **`dawnop.com/gpu/`**。

技术栈：Vite + Vue 3（外壳/状态）+ Three.js（3D，`InstancedMesh` 画上千个线程立方体）。

## 开发
```bash
cd gpu-viz
npm install
npm run dev        # http://localhost:5173/gpu/ （base 为 /gpu/）
```
> 若同时在跑博客的 `frontend` dev（也用 5173），给其中一个指定别的端口：`npm run dev -- --port 5180`。

## 构建
```bash
npm run build      # 产物在 gpu-viz/dist/（base=/gpu/，资源路径已带 /gpu/ 前缀）
npm run preview -- --port 4188   # 本地预览构建产物：http://localhost:4188/gpu/
```

## 部署到 dawnop.com/gpu/
1. 传产物到**独立目录**（不要放进博客的 `/var/www/dawnop/dist`，否则会被博客发版的
   `rsync --delete` 删掉）：
   ```bash
   rsync -az --delete gpu-viz/dist/ <user>@<server>:/var/www/dawnop/gpu/
   ```
2. 在站点 nginx 配置（`/etc/nginx/sites-available/dawnop`）里，**`location /` 之前**加一段：
   ```nginx
   location /gpu/ {
       alias /var/www/dawnop/gpu/;
       try_files $uri $uri/ /gpu/index.html;
   }
   ```
   然后 `sudo nginx -t && sudo systemctl reload nginx`。
3. 访问 `https://dawnop.com/gpu/` 验证。

## 代码结构
```
src/
├── main.js
├── App.vue                 # 布局：3D 视口 + 侧边讲解面板，持有 topic/mode 状态
├── style.css               # 暗色主题（GPU 绿强调色）
├── content/topics.js       # 主题数据（文本/代码/模式）—— 加新主题在此追加
├── components/
│   ├── Viewport.vue        # 挂载 Three.js 场景的 canvas
│   └── SidePanel.vue       # 主题导航 + 讲解 + 模式切换 + 代码
└── three/gpuScene.js       # Three.js 引擎：线程模型场景 + blocks/warps/thread 三种着色
```

## 扩展新主题
1. 在 `content/topics.js` 加一项（`hasScene:false` 会显示「开发中」占位）。
2. 在 `three/` 下为该主题写一个 `build<Topic>()` 场景（tiling/pipeline/cache 等），
   在 `gpuScene.js` 里按 `topic.id` 切换构建，并把 `hasScene` 置 `true`。

> 当前已实现：**线程模型**（Grid 4×4 × Block 8×8 = 1024 线程的 `InstancedMesh`，
> 含 Block 划分 / Warp 逐组高亮 / 单线程 三种模式）。其余主题为文本占位，待补 3D 场景。
