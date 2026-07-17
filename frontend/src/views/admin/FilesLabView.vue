<script setup>
// 自建文件管理器（SVAR 观感 / Element Plus）。第三期：右键菜单、拖拽上传、
// 多选批量操作、面板宽度可拖拽、并行上传/批量下载。
// 布局：左「目录树 + 存储条」/ 顶「工具栏」/ 中「列表·网格」/ 右「点文件弹出的预览面板」。
// 领域逻辑全部在 composables/useFileManager.js；本 SFC 只做「解构状态/方法 + 渲染」。
// 下方从 @element-plus/icons-vue 导入的图标仅供模板直接使用（<Upload/> 等）。
import {
  Folder,
  FolderOpened,
  FolderAdd,
  Files,
  Upload,
  Search,
  MoreFilled,
  Download,
  EditPen,
  Delete,
  View,
  Close,
  Loading,
  Right,
  CopyDocument,
  ArrowDown,
  ArrowUp,
  Plus,
  Select,
} from '@element-plus/icons-vue'
import { useFileManager } from '../../composables/useFileManager'

const {
  fm,
  cwd,
  loading,
  q,
  viewMode,
  showInfo,
  selectedPath,
  selPaths,
  previewText,
  previewErr,
  isMobile,
  selectMode,
  searchOpen,
  searchInputRef,
  sheet,
  effectiveView,
  drive,
  drivePct,
  tasks,
  tasksCollapsed,
  activeCount,
  treeRef,
  treeKey,
  tableRef,
  fileInput,
  dirInput,
  sideW,
  infoW,
  viewOptions,
  treeProps,
  filtered,
  selected,
  selRows,
  selFiles,
  contentEl,
  dnd,
  marquee,
  marqueeStyle,
  crumbs,
  rowClass,
  imgViewer,
  modal,
  destDlg,
  dragging,
  ctx,
  ctxItems,
  openSearch,
  closeSearch,
  goto,
  loadTreeNode,
  onTreeClick,
  onSelChange,
  clearSel,
  onRowClick,
  onRowDblclick,
  onBlankClick,
  openImgViewer,
  startEdit,
  saveEdit,
  beforeCloseModal,
  onCellTap,
  toggleSelectMode,
  openSheet,
  sheetDo,
  doDownload,
  doDownloadMany,
  newFolder,
  doRename,
  doDeleteMany,
  onRowCommand,
  startMoveCopy,
  confirmDest,
  onDragStartRow,
  onDragEndRow,
  onDestDragOver,
  onDestDragLeave,
  onDestDrop,
  onRowDragOver,
  onRowDragLeave,
  onRowDrop,
  onContentMousedown,
  openCtxRow,
  openCtxBlank,
  onTableRowCtx,
  runCtx,
  pickFiles,
  pickFolder,
  onFilesPicked,
  onDragEnter,
  onDragLeave,
  onDrop,
  startResize,
  isImage,
  isText,
  iconOf,
  tintOf,
  fmtSize,
  fmtDate,
} = useFileManager()
</script>

<template>
  <div class="fm">
    <div class="fm-card">
      <!-- 左侧：目录树 + 存储条（移动端隐藏，改用面包屑导航） -->
      <aside v-if="!isMobile" class="fm-side" :style="{ width: sideW + 'px' }">
        <el-dropdown trigger="click" @command="(c) => (c === 'file' ? pickFiles() : pickFolder())">
          <el-button type="primary" class="fm-upload" :icon="Upload">上传</el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="file" :icon="Files">上传文件</el-dropdown-item>
              <el-dropdown-item command="folder" :icon="FolderOpened">上传文件夹</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <input ref="fileInput" type="file" multiple hidden @change="onFilesPicked" />
        <input ref="dirInput" type="file" multiple webkitdirectory hidden @change="onFilesPicked" />

        <div class="fm-tree">
          <el-tree
            :key="treeKey"
            ref="treeRef"
            lazy
            :load="loadTreeNode"
            :props="treeProps"
            node-key="path"
            :current-node-key="cwd"
            :expand-on-click-node="false"
            :default-expanded-keys="['']"
            highlight-current
            @node-click="onTreeClick"
          >
            <template #default="{ node }">
              <span
                class="tnode"
                :class="{ 'drop-into': dnd.dragging && dnd.overPath === node.data.path }"
                @dragover="onDestDragOver($event, node.data.path)"
                @dragleave="onDestDragLeave(node.data.path)"
                @drop="onDestDrop($event, node.data.path)"
              >
                <el-icon class="ti"><FolderOpened v-if="node.expanded" /><Folder v-else /></el-icon>
                <span class="tlabel">{{ node.label }}</span>
              </span>
            </template>
          </el-tree>
        </div>

        <div class="fm-drive">
          <el-progress :percentage="drivePct" :stroke-width="6" :show-text="false" />
          <div class="fm-drive-t">
            <template v-if="drive">
              已用 {{ drive.used ? fmtSize(drive.used) : '0 B' }} / {{ fmtSize(drive.quota) }} ·
              {{ drive.files }} 个文件
            </template>
            <template v-else>用量统计加载中…</template>
          </div>
        </div>
      </aside>

      <div v-if="!isMobile" class="fm-rs" @mousedown.prevent="startResize('side', $event)"></div>

      <!-- 中间：工具栏 + 面包屑 + 内容（整块可拖入上传） -->
      <section
        class="fm-main"
        @dragenter.prevent="onDragEnter"
        @dragover.prevent
        @dragleave="onDragLeave"
        @drop.prevent="onDrop"
      >
        <!-- 桌面工具栏（移动端改用右下角悬浮按钮 + 搜索窗口 + 多选头） -->
        <div v-if="!isMobile" class="fm-toolbar">
          <div class="fm-tb-left">
            <template v-if="selPaths.length">
              <span class="fm-selinfo">已选 {{ selPaths.length }} 项</span>
              <el-button
                text
                :icon="Download"
                :disabled="!selFiles.length"
                @click="doDownloadMany(selRows)"
                >下载</el-button
              >
              <el-button text :icon="Right" @click="startMoveCopy('move', selRows)">移动</el-button>
              <el-button text :icon="CopyDocument" @click="startMoveCopy('copy', selRows)"
                >复制</el-button
              >
              <el-button text type="danger" :icon="Delete" @click="doDeleteMany(selRows)"
                >删除</el-button
              >
              <el-button text :icon="Close" @click="clearSel">取消选择</el-button>
            </template>
            <template v-else>
              <el-button :icon="FolderAdd" text @click="newFolder">新建文件夹</el-button>
            </template>
          </div>
          <div class="fm-tb-right">
            <el-input
              v-model="q"
              :prefix-icon="Search"
              placeholder="搜索当前目录"
              clearable
              class="fm-search"
            />
            <el-segmented v-model="viewMode" :options="viewOptions" class="fm-seg">
              <template #default="{ item }"
                ><el-icon><component :is="item.icon" /></el-icon
              ></template>
            </el-segmented>
            <el-button
              :icon="View"
              circle
              :type="showInfo ? 'primary' : ''"
              :plain="showInfo"
              title="预览面板"
              @click="showInfo = !showInfo"
            />
          </div>
        </div>

        <!-- 移动端多选态顶栏：已选计数 + 取消 -->
        <div v-if="isMobile && selectMode" class="fm-selhead">
          <span class="fm-selhead-count">{{
            selPaths.length ? `已选 ${selPaths.length} 项` : '选择文件'
          }}</span>
          <el-button text @click="toggleSelectMode">取消</el-button>
        </div>

        <!-- 移动端搜索态顶栏：复用顶部条，就地过滤下方网格 -->
        <div v-else-if="isMobile && searchOpen" class="fm-selhead">
          <el-input
            ref="searchInputRef"
            v-model="q"
            :prefix-icon="Search"
            placeholder="搜索当前目录"
            clearable
            class="fm-searchbar-input"
          />
          <el-button text @click="closeSearch">取消</el-button>
        </div>

        <div class="fm-crumb">
          <el-breadcrumb separator="/">
            <el-breadcrumb-item v-for="c in crumbs" :key="c.path">
              <a class="crumb" @click="goto(c.path)">{{ c.name }}</a>
            </el-breadcrumb-item>
          </el-breadcrumb>
        </div>

        <div
          v-loading="loading"
          ref="contentEl"
          class="fm-content"
          @contextmenu="openCtxBlank"
          @click="onBlankClick"
          @mousedown="onContentMousedown"
        >
          <!-- 列表 -->
          <el-table
            v-if="effectiveView === 'list'"
            ref="tableRef"
            :data="filtered"
            row-key="path"
            :row-class-name="rowClass"
            @row-click="onRowClick"
            @row-dblclick="onRowDblclick"
            @row-contextmenu="onTableRowCtx"
            @selection-change="onSelChange"
          >
            <el-table-column type="selection" width="40" :reserve-selection="false" />
            <el-table-column label="名称" min-width="260">
              <template #default="{ row }">
                <span
                  class="namecell"
                  :class="{ 'drop-into': row.is_dir && dnd.overPath === row.path }"
                  draggable="true"
                  @dragstart="onDragStartRow($event, row)"
                  @dragend="onDragEndRow"
                  @dragover="onRowDragOver($event, row)"
                  @dragleave="onRowDragLeave(row)"
                  @drop="onRowDrop($event, row)"
                >
                  <el-icon class="nc-ico" :style="{ color: tintOf(row) }"
                    ><component :is="iconOf(row)"
                  /></el-icon>
                  <span class="nc-name">{{ row.name }}</span>
                </span>
              </template>
            </el-table-column>
            <el-table-column label="大小" width="120" align="right">
              <template #default="{ row }"
                ><span class="muted">{{ row.is_dir ? '—' : fmtSize(row.size) }}</span></template
              >
            </el-table-column>
            <el-table-column label="修改时间" width="130">
              <template #default="{ row }"
                ><span class="muted">{{ fmtDate(row.updated_at) }}</span></template
              >
            </el-table-column>
            <el-table-column width="56" align="center">
              <template #default="{ row }">
                <el-dropdown trigger="click" @command="(c) => onRowCommand(c, row)">
                  <el-icon class="rowmore" @click.stop><MoreFilled /></el-icon>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item :icon="Download" command="下载" :disabled="row.is_dir"
                        >下载</el-dropdown-item
                      >
                      <el-dropdown-item :icon="EditPen" command="重命名">重命名</el-dropdown-item>
                      <el-dropdown-item :icon="Delete" command="删除" divided
                        >删除</el-dropdown-item
                      >
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </template>
            </el-table-column>
            <template #empty
              ><el-empty description="空目录，可直接拖文件进来" :image-size="80"
            /></template>
          </el-table>

          <!-- 网格 -->
          <div v-else class="fm-grid">
            <div
              v-for="row in filtered"
              :key="row.path"
              class="cell"
              :class="{
                'is-sel': row.path === selectedPath,
                'is-checked': selPaths.includes(row.path),
                'drop-into': row.is_dir && dnd.overPath === row.path,
              }"
              :draggable="!isMobile"
              @click="onCellTap(row, $event)"
              @dblclick="onRowDblclick(row)"
              @contextmenu="openCtxRow($event, row)"
              @dragstart="onDragStartRow($event, row)"
              @dragend="onDragEndRow"
              @dragover="onRowDragOver($event, row)"
              @dragleave="onRowDragLeave(row)"
              @drop="onRowDrop($event, row)"
            >
              <button v-if="isMobile && !selectMode" class="cell-more" @click.stop="openSheet(row)">
                <el-icon><MoreFilled /></el-icon>
              </button>
              <el-icon
                v-if="isMobile && selectMode"
                class="cell-check"
                :class="{ on: selPaths.includes(row.path) }"
                ><Select
              /></el-icon>
              <div class="cell-thumb">
                <img
                  v-if="isImage(row)"
                  :src="fm.previewUrl(row.path, { w: 320, h: 200, mode: 'fill' })"
                  class="cell-img"
                  loading="lazy"
                  alt=""
                />
                <el-icon v-else class="cell-ico" :style="{ color: tintOf(row) }"
                  ><component :is="iconOf(row)"
                /></el-icon>
              </div>
              <div class="cell-name">{{ row.name }}</div>
            </div>
            <el-empty
              v-if="!filtered.length && !loading"
              description="空目录，可直接拖文件进来"
              :image-size="80"
              class="grid-empty"
            />
          </div>
        </div>

        <!-- 拖入蒙层 -->
        <div v-if="dragging" class="fm-drop">
          <el-icon :size="36"><Upload /></el-icon>
          <span>释放以上传到「{{ crumbs[crumbs.length - 1].name }}」</span>
        </div>
      </section>

      <!-- 右侧：预览 + 信息（移动端改用底部弹层，不渲染此面板） -->
      <template v-if="showInfo && !isMobile">
        <div class="fm-rs" @mousedown.prevent="startResize('info', $event)"></div>
        <aside class="fm-info" :style="{ width: infoW + 'px' }">
          <div class="fm-info-head">
            <span class="fm-info-title">{{ selected ? selected.name : '预览' }}</span>
            <el-icon class="fm-info-close" title="收起" @click="showInfo = false"
              ><Close
            /></el-icon>
          </div>
          <template v-if="selected && !selected.is_dir">
            <div class="fm-preview">
              <div
                v-if="isImage(selected)"
                class="pv-img-wrap"
                title="点击放大"
                @click="openImgViewer(selected)"
              >
                <img
                  :src="fm.previewUrl(selected.path, { w: 900, mode: 'fit' })"
                  class="pv-img"
                  alt=""
                />
              </div>
              <pre v-else-if="isText(selected)" class="pv-text">{{
                previewErr || previewText || '加载中…'
              }}</pre>
              <div v-else class="pv-file">
                <el-icon :size="56" :style="{ color: tintOf(selected) }"
                  ><component :is="iconOf(selected)"
                /></el-icon>
              </div>
            </div>
            <el-descriptions :column="1" size="small" border class="fm-desc">
              <el-descriptions-item label="类型">{{
                selected.content_type || '文件'
              }}</el-descriptions-item>
              <el-descriptions-item label="大小">{{ fmtSize(selected.size) }}</el-descriptions-item>
              <el-descriptions-item label="修改时间">{{
                fmtDate(selected.updated_at)
              }}</el-descriptions-item>
            </el-descriptions>
            <div class="fm-info-actions">
              <el-button :icon="Download" @click="doDownload(selected)">下载</el-button>
              <el-button :icon="EditPen" @click="doRename(selected)">重命名</el-button>
            </div>
          </template>
          <el-empty v-else description="选择一个文件查看预览" :image-size="90" />
        </aside>
      </template>
    </div>

    <!-- 双击的文本预览/编辑弹窗（图片走 el-image-viewer） -->
    <el-dialog
      v-model="modal.show"
      :title="modal.row?.name"
      :width="isMobile ? '94%' : '72%'"
      :top="isMobile ? '4vh' : '6vh'"
      append-to-body
      :before-close="beforeCloseModal"
    >
      <div v-if="modal.row" class="fm-modal-body">
        <template v-if="isText(modal.row)">
          <el-input
            v-if="modal.editing"
            v-model="modal.draft"
            type="textarea"
            :rows="22"
            resize="none"
            class="fm-modal-edit"
          />
          <pre v-else class="fm-modal-text">{{ modal.err || modal.text || '加载中…' }}</pre>
        </template>
        <div v-else class="fm-modal-file">
          <el-icon :size="72" :style="{ color: tintOf(modal.row) }"
            ><component :is="iconOf(modal.row)"
          /></el-icon>
          <span class="fm-modal-hint">该类型暂不支持在线预览</span>
          <el-button type="primary" :icon="Download" @click="doDownload(modal.row)">下载</el-button>
        </div>
      </div>
      <template v-if="modal.row && isText(modal.row)" #footer>
        <template v-if="modal.editing">
          <el-button @click="modal.editing = false">取消</el-button>
          <el-button type="primary" :loading="modal.saving" @click="saveEdit">保存</el-button>
        </template>
        <template v-else>
          <el-button :icon="Download" @click="doDownload(modal.row)">下载</el-button>
          <el-button type="primary" :icon="EditPen" :disabled="!modal.loaded" @click="startEdit"
            >编辑</el-button
          >
        </template>
      </template>
    </el-dialog>

    <!-- 图片查看器：滚轮/工具栏缩放、旋转 -->
    <el-image-viewer
      v-if="imgViewer.show"
      :url-list="imgViewer.urls"
      hide-on-click-modal
      teleported
      @close="imgViewer.show = false"
    />

    <!-- 传输列表：上传/下载进度（右下角浮层） -->
    <div v-if="tasks.length" class="fm-tasks">
      <div class="fm-tasks-head" @click="tasksCollapsed = !tasksCollapsed">
        <span class="fm-tasks-title">
          <el-icon v-if="activeCount" class="spin"><Loading /></el-icon>
          传输列表{{ activeCount ? `（${activeCount} 进行中）` : '' }}
        </span>
        <span class="fm-tasks-ops">
          <el-icon v-if="!activeCount" title="清除记录" @click.stop="tasks = []"
            ><Delete
          /></el-icon>
          <el-icon><ArrowDown v-if="!tasksCollapsed" /><ArrowUp v-else /></el-icon>
        </span>
      </div>
      <div v-show="!tasksCollapsed" class="fm-tasks-body">
        <div v-for="t in tasks" :key="t.id" class="fm-task">
          <el-icon class="ft-kind"><Upload v-if="t.kind === 'up'" /><Download v-else /></el-icon>
          <div class="ft-main">
            <div class="ft-name">{{ t.name }}</div>
            <el-progress
              :percentage="t.pct"
              :stroke-width="4"
              :show-text="false"
              :status="
                t.status === 'error' ? 'exception' : t.status === 'done' ? 'success' : undefined
              "
            />
            <div v-if="t.err" class="ft-err">{{ t.err }}</div>
          </div>
          <span class="ft-pct" :class="t.status">
            {{ t.status === 'done' ? '完成' : t.status === 'error' ? '失败' : `${t.pct}%` }}
          </span>
        </div>
      </div>
    </div>

    <!-- 右键菜单（teleport 到 body，不用 CSS 变量以免脱离 .fm 作用域后失效） -->
    <Teleport to="body">
      <div
        v-if="ctx.show"
        class="fm-ctx"
        :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }"
        @contextmenu.prevent
        @click.stop
      >
        <template v-for="(item, i) in ctxItems" :key="i">
          <div v-if="item.divided" class="fm-ctx-divider"></div>
          <div class="fm-ctx-item" :class="{ danger: item.danger }" @click="runCtx(item)">
            <el-icon><component :is="item.icon" /></el-icon>
            <span>{{ item.label }}</span>
          </div>
        </template>
      </div>
    </Teleport>

    <!-- 移动/复制目标目录选择 -->
    <el-dialog
      v-model="destDlg.show"
      :title="`${destDlg.mode === 'move' ? '移动' : '复制'} ${destDlg.rows.length} 项到…`"
      :width="isMobile ? '92%' : '420px'"
      append-to-body
    >
      <div class="dest-tree">
        <el-tree
          :key="`dest-${treeKey}-${destDlg.show}`"
          lazy
          :load="loadTreeNode"
          :props="treeProps"
          node-key="path"
          :expand-on-click-node="false"
          :default-expanded-keys="['']"
          highlight-current
          @node-click="(d) => (destDlg.path = d.path)"
        >
          <template #default="{ node }">
            <span class="tnode">
              <el-icon class="ti"><FolderOpened v-if="node.expanded" /><Folder v-else /></el-icon>
              <span class="tlabel">{{ node.label }}</span>
            </span>
          </template>
        </el-tree>
      </div>
      <template #footer>
        <el-button @click="destDlg.show = false">取消</el-button>
        <el-button
          type="primary"
          :disabled="destDlg.path === null"
          :loading="destDlg.busy"
          @click="confirmDest"
        >
          {{ destDlg.mode === 'move' ? '移动到此' : '复制到此' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 框选选择框（视口固定坐标） -->
    <div v-if="marquee.show" class="fm-marquee" :style="marqueeStyle"></div>

    <!-- 移动端右下角悬浮按钮组（自下而上：+ / 搜索 / 多选）；多选态与搜索窗内全部隐藏 -->
    <template v-if="isMobile && !selectMode && !searchOpen">
      <el-button
        circle
        :icon="Select"
        class="fm-fab-mini fm-fab-select"
        title="多选"
        @click="toggleSelectMode"
      />
      <el-button
        circle
        :icon="Search"
        class="fm-fab-mini fm-fab-search"
        title="搜索"
        @click="openSearch"
      />
    </template>
    <el-dropdown
      v-if="isMobile && !selectMode && !searchOpen"
      trigger="click"
      class="fm-fab"
      placement="top-end"
      @command="(c) => (c === 'file' ? pickFiles() : c === 'folder' ? pickFolder() : newFolder())"
    >
      <el-button type="primary" circle :icon="Plus" class="fm-fab-btn" />
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="file" :icon="Files">上传文件</el-dropdown-item>
          <el-dropdown-item command="folder" :icon="FolderOpened">上传文件夹</el-dropdown-item>
          <el-dropdown-item command="newfolder" :icon="FolderAdd" divided
            >新建文件夹</el-dropdown-item
          >
        </el-dropdown-menu>
      </template>
    </el-dropdown>

    <!-- 移动端：多选态底部批量操作条 -->
    <div v-if="isMobile && selectMode && selPaths.length" class="fm-selbar">
      <button class="fm-selbar-btn" :disabled="!selFiles.length" @click="doDownloadMany(selRows)">
        <el-icon><Download /></el-icon><span>下载</span>
      </button>
      <button class="fm-selbar-btn" @click="startMoveCopy('move', selRows)">
        <el-icon><Right /></el-icon><span>移动</span>
      </button>
      <button class="fm-selbar-btn" @click="startMoveCopy('copy', selRows)">
        <el-icon><CopyDocument /></el-icon><span>复制</span>
      </button>
      <button class="fm-selbar-btn danger" @click="doDeleteMany(selRows)">
        <el-icon><Delete /></el-icon><span>删除</span>
      </button>
    </div>

    <!-- 移动端：底部操作弹层 -->
    <el-drawer
      v-model="sheet.show"
      direction="btt"
      size="auto"
      :with-header="false"
      class="fm-sheet"
    >
      <div v-if="sheet.row" class="fm-sheet-inner">
        <div class="fm-sheet-head">
          <el-icon class="fm-sheet-ico" :style="{ color: tintOf(sheet.row) }">
            <component :is="iconOf(sheet.row)" />
          </el-icon>
          <div class="fm-sheet-name">{{ sheet.row.name }}</div>
        </div>
        <div class="fm-sheet-grid">
          <button v-if="!sheet.row.is_dir" class="fm-sheet-btn" @click="sheetDo('preview')">
            <el-icon><View /></el-icon><span>预览</span>
          </button>
          <button v-if="!sheet.row.is_dir" class="fm-sheet-btn" @click="sheetDo('download')">
            <el-icon><Download /></el-icon><span>下载</span>
          </button>
          <button class="fm-sheet-btn" @click="sheetDo('rename')">
            <el-icon><EditPen /></el-icon><span>重命名</span>
          </button>
          <button class="fm-sheet-btn" @click="sheetDo('move')">
            <el-icon><Right /></el-icon><span>移动</span>
          </button>
          <button class="fm-sheet-btn" @click="sheetDo('copy')">
            <el-icon><CopyDocument /></el-icon><span>复制</span>
          </button>
          <button class="fm-sheet-btn danger" @click="sheetDo('delete')">
            <el-icon><Delete /></el-icon><span>删除</span>
          </button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
/* ---------- Willow 皮：token ---------- */
.fm {
  --w-border: #ededf1;
  --w-border-soft: #f2f2f5;
  --w-side-bg: #fbfbfc;
  --w-sel: #eaedf5;
  --w-muted: #9fa1ae;
  --w-shadow: 0 1px 2px rgba(44, 47, 60, 0.06), 0 3px 10px rgba(44, 47, 60, 0.1);

  flex: 1;
  min-height: 0;
  display: flex;
  position: relative; /* 传输列表浮层的定位基准 */
}
.fm-card {
  flex: 1;
  min-height: 0;
  display: flex;
  background: #fff;
  border: 1px solid var(--w-border);
  border-radius: 12px;
  box-shadow: var(--w-shadow);
  overflow: hidden;
}

/* ---------- 左侧 ---------- */
.fm-side {
  flex-shrink: 0;
  border-right: 1px solid var(--w-border);
  background: var(--w-side-bg);
  display: flex;
  flex-direction: column;
  padding: 14px 12px;
  gap: 12px;
}
.fm-upload {
  width: 100%;
}
.fm-tree {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.fm-tree :deep(.el-tree) {
  background: transparent;
}
.fm-tree :deep(.el-tree-node__content) {
  height: 34px;
  border-radius: 8px;
}
.tnode {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.9rem;
}
.ti {
  color: #e8a13a;
}
.fm-drive {
  border-top: 1px solid var(--w-border);
  padding-top: 12px;
}
.fm-drive-t {
  font-size: 12px;
  color: var(--w-muted);
  margin-top: 7px;
}

/* ---------- 拖拽分隔条 ---------- */
.fm-rs {
  width: 5px;
  margin: 0 -2.5px;
  flex-shrink: 0;
  cursor: col-resize;
  z-index: 3;
  transition: background 0.15s;
}
.fm-rs:hover,
.fm-rs:active {
  background: var(--el-color-primary-light-7);
}

/* ---------- 中间 ---------- */
.fm-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  position: relative;
}
.fm-toolbar {
  height: 56px;
  flex-shrink: 0;
  border-bottom: 1px solid var(--w-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 14px;
  gap: 12px;
}
.fm-tb-left {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.fm-selinfo {
  font-size: 0.88rem;
  color: var(--el-text-color-primary);
  font-weight: 600;
  margin-right: 6px;
  white-space: nowrap;
}
.spin {
  animation: fm-spin 1s linear infinite;
}
@keyframes fm-spin {
  to {
    transform: rotate(360deg);
  }
}
.fm-tb-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.fm-search {
  width: 220px;
}
.fm-crumb {
  padding: 11px 16px;
  border-bottom: 1px solid var(--w-border-soft);
}
.crumb {
  cursor: pointer;
  color: var(--el-text-color-regular);
}
.crumb:hover {
  color: var(--el-color-primary);
}
.fm-content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 6px 10px 10px;
}

/* 拖入蒙层（pointer-events:none，drop 落在下层元素上冒泡到 fm-main） */
.fm-drop {
  position: absolute;
  inset: 0;
  z-index: 5;
  pointer-events: none;
  background: rgba(22, 119, 255, 0.06);
  border: 2px dashed var(--el-color-primary);
  border-radius: 0 12px 12px 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--el-color-primary);
  font-size: 0.95rem;
  font-weight: 600;
}

/* 拖动移动：落点文件夹高亮 */
.namecell.drop-into,
.tnode.drop-into {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
  border-radius: 6px;
  background: var(--el-color-primary-light-9);
}
.cell.drop-into {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 2px var(--el-color-primary) inset;
  background: var(--el-color-primary-light-9);
}
.tnode {
  border-radius: 6px;
}

/* 框选选择框 */
.fm-marquee {
  position: fixed;
  z-index: 2500;
  pointer-events: none;
  border: 1px solid var(--el-color-primary);
  background: rgba(22, 119, 255, 0.1);
  border-radius: 2px;
}

/* 列表 */
.namecell {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  cursor: grab;
}
.nc-ico {
  font-size: 18px;
}
.nc-name {
  color: var(--el-text-color-primary);
}
.muted {
  color: var(--w-muted);
  font-size: 0.86rem;
}
.rowmore {
  cursor: pointer;
  color: var(--w-muted);
  padding: 4px;
  border-radius: 6px;
}
.rowmore:hover {
  background: #f0f2f5;
  color: var(--el-text-color-primary);
}
.fm-content :deep(.el-table__row) {
  cursor: pointer;
}
.fm-content :deep(.el-table__row.is-sel > td) {
  background: var(--w-sel);
}
.fm-content :deep(.el-table__row.is-sel:hover > td) {
  background: var(--w-sel);
}

/* 网格 */
.fm-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(132px, 1fr));
  gap: 12px;
  padding: 12px 8px;
}
.cell {
  position: relative;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  padding: 12px;
  cursor: pointer;
  transition:
    border-color 0.15s,
    box-shadow 0.15s;
}
/* 移动端网格卡片右上角 ⋮ 操作入口 */
.cell-more {
  position: absolute;
  top: 4px;
  right: 4px;
  z-index: 2;
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.82);
  color: #606266;
  cursor: pointer;
}
/* 选择模式的勾选标 */
.cell-check {
  position: absolute;
  top: 6px;
  left: 6px;
  z-index: 2;
  font-size: 18px;
  color: #c0c4cc;
  background: #fff;
  border-radius: 50%;
}
.cell-check.on {
  color: var(--el-color-primary);
}
.cell:hover {
  box-shadow: var(--w-shadow);
}
.cell.is-sel {
  background: var(--w-sel);
  border-color: #d5d9e6;
}
.cell.is-checked {
  border-color: var(--el-color-primary);
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
}
.cell-thumb {
  height: 78px;
  border-radius: 8px;
  background: #f4f5f8;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 10px;
  overflow: hidden;
}
.cell-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.cell-ico {
  font-size: 34px;
}
.cell-name {
  font-size: 0.85rem;
  text-align: center;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.grid-empty {
  grid-column: 1 / -1;
}

/* ---------- 右侧 ---------- */
.fm-info {
  flex-shrink: 0;
  border-left: 1px solid var(--w-border);
  background: var(--w-side-bg);
  padding: 16px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.fm-info-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.fm-info-title {
  font-weight: 600;
  font-size: 0.92rem;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fm-info-close {
  cursor: pointer;
  color: var(--w-muted);
  padding: 4px;
  border-radius: 6px;
  flex-shrink: 0;
}
.fm-info-close:hover {
  background: #f0f2f5;
  color: var(--el-text-color-primary);
}
/* 预览占大头：撑满面板剩余高度，信息/操作固定在底部 */
.fm-preview {
  flex: 1;
  min-height: 200px;
  display: flex;
  border-radius: 10px;
  overflow: hidden;
}
.pv-img-wrap {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  cursor: zoom-in;
}
.pv-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
}
.pv-text {
  margin: 0;
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: #fff;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  padding: 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.pv-file {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--w-border);
  border-radius: 10px;
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
}
.fm-desc {
  flex-shrink: 0;
}
.fm-desc :deep(.el-descriptions__label) {
  color: var(--w-muted);
  width: 72px;
}
.fm-info-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.fm-info-actions .el-button {
  flex: 1;
}

/* ---------- 双击预览弹窗 ---------- */
.fm-modal-body {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}
.fm-modal-edit {
  width: 100%;
}
.fm-modal-edit :deep(.el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  line-height: 1.7;
  max-height: 72vh;
}
.fm-modal-text {
  margin: 0;
  width: 100%;
  max-height: 72vh;
  overflow: auto;
  background: #fafbfc;
  border: 1px solid #ededf1;
  border-radius: 10px;
  padding: 14px 16px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre-wrap;
  word-break: break-word;
}
.fm-modal-file {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 28px 0;
}
.fm-modal-hint {
  color: #9fa1ae;
  font-size: 0.9rem;
}

/* ---------- 传输列表（右下角浮层） ---------- */
.fm-tasks {
  position: absolute;
  right: 18px;
  bottom: 18px;
  z-index: 20;
  width: 320px;
  background: #fff;
  border: 1px solid var(--w-border);
  border-radius: 12px;
  box-shadow: var(--w-shadow);
  overflow: hidden;
}
.fm-tasks-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  cursor: pointer;
  background: var(--w-side-bg);
  border-bottom: 1px solid var(--w-border);
  font-size: 0.88rem;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.fm-tasks-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}
.fm-tasks-ops {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--w-muted);
}
.fm-tasks-ops .el-icon {
  cursor: pointer;
  padding: 3px;
  border-radius: 5px;
}
.fm-tasks-ops .el-icon:hover {
  background: #f0f2f5;
  color: var(--el-text-color-primary);
}
.fm-tasks-body {
  max-height: 260px;
  overflow: auto;
  padding: 6px 0;
}
.fm-task {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 14px;
}
.ft-kind {
  color: var(--w-muted);
  flex-shrink: 0;
}
.ft-main {
  flex: 1;
  min-width: 0;
}
.ft-name {
  font-size: 0.82rem;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-bottom: 4px;
}
.ft-err {
  font-size: 0.76rem;
  color: var(--el-color-danger);
  margin-top: 3px;
}
.ft-pct {
  font-size: 0.78rem;
  color: var(--w-muted);
  flex-shrink: 0;
  width: 38px;
  text-align: right;
}
.ft-pct.done {
  color: var(--el-color-success);
}
.ft-pct.error {
  color: var(--el-color-danger);
}

/* ---------- 右键菜单（teleport 到 body，颜色写死不依赖 .fm 的变量） ---------- */
.fm-ctx {
  position: fixed;
  z-index: 3000;
  min-width: 172px;
  background: #fff;
  border: 1px solid #ededf1;
  border-radius: 10px;
  box-shadow:
    0 1px 2px rgba(44, 47, 60, 0.06),
    0 3px 10px rgba(44, 47, 60, 0.12);
  padding: 6px;
}
.fm-ctx-item {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 10px;
  border-radius: 6px;
  font-size: 0.88rem;
  cursor: pointer;
  color: var(--el-text-color-primary);
  white-space: nowrap;
}
.fm-ctx-item:hover {
  background: #f0f2f5;
}
.fm-ctx-item.danger {
  color: var(--el-color-danger);
}
.fm-ctx-item .el-icon {
  color: #9fa1ae;
}
.fm-ctx-item.danger .el-icon {
  color: var(--el-color-danger);
}
.fm-ctx-divider {
  height: 1px;
  background: #ededf1;
  margin: 5px 4px;
}

/* ---------- 目标目录弹窗 ---------- */
.dest-tree {
  max-height: 320px;
  overflow: auto;
  border: 1px solid #ededf1;
  border-radius: 10px;
  padding: 8px;
}
.dest-tree :deep(.el-tree-node__content) {
  height: 32px;
  border-radius: 6px;
}

/* 窄屏：收起右侧预览面板 */
@media (max-width: 900px) {
  .fm-side {
    width: 180px !important;
  }
  .fm-info,
  .fm-rs {
    display: none;
  }
}

/* ---------- 移动端悬浮 + 按钮 ---------- */
.fm-fab {
  position: fixed;
  right: 18px;
  bottom: 22px;
  z-index: 1500;
}
.fm-fab-btn {
  width: 52px;
  height: 52px;
  font-size: 22px;
  box-shadow: 0 6px 18px rgba(22, 119, 255, 0.38);
}
/* 次级悬浮按钮（搜索 / 多选）：叠在 + 之上，白底 */
.fm-fab-mini {
  position: fixed;
  right: 21px;
  z-index: 1500;
  width: 46px;
  height: 46px;
  font-size: 18px;
  background: #fff;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.16);
}
.fm-fab-search {
  bottom: 84px;
}
.fm-fab-select {
  bottom: 140px;
}

/* ---------- 移动端多选顶栏 ---------- */
.fm-selhead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 52px;
  flex-shrink: 0;
  padding: 0 6px 0 14px;
  border-bottom: 1px solid var(--w-border);
}
.fm-selhead-count {
  font-size: 0.95rem;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
/* 搜索态复用顶栏，输入框铺满 */
.fm-searchbar-input {
  flex: 1;
}

/* ---------- 移动端多选底部批量操作条 ---------- */
.fm-selbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 1400;
  display: flex;
  background: #fff;
  border-top: 1px solid var(--w-border);
  box-shadow: 0 -2px 14px rgba(0, 0, 0, 0.07);
  padding: 6px 4px calc(6px + env(safe-area-inset-bottom, 0px));
}
.fm-selbar-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 2px;
  border: none;
  background: none;
  color: #3c4149;
  font-size: 0.74rem;
  cursor: pointer;
}
.fm-selbar-btn .el-icon {
  font-size: 20px;
}
.fm-selbar-btn.danger {
  color: var(--el-color-danger);
}
.fm-selbar-btn:disabled {
  opacity: 0.38;
}

/* ---------- 移动端底部操作弹层 ---------- */
.fm-sheet :deep(.el-drawer__body) {
  padding: 0;
}
.fm-sheet-inner {
  padding: 8px 12px 20px;
}
.fm-sheet-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 6px 14px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.fm-sheet-ico {
  font-size: 26px;
  flex-shrink: 0;
}
.fm-sheet-name {
  font-weight: 600;
  font-size: 0.95rem;
  word-break: break-all;
}
.fm-sheet-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  padding-top: 14px;
}
.fm-sheet-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
  padding: 14px 6px;
  border: none;
  border-radius: 12px;
  background: #f5f7fa;
  color: #3c4149;
  font-size: 0.8rem;
  cursor: pointer;
}
.fm-sheet-btn .el-icon {
  font-size: 21px;
}
.fm-sheet-btn.danger {
  color: var(--el-color-danger);
}

/* ---------- 移动端整体布局（≤768） ---------- */
@media (max-width: 768px) {
  /* 顶部工具栏移动端不渲染（改用悬浮按钮 + 搜索窗 + 多选顶栏） */
  .fm-content {
    padding: 6px;
  }
  .fm-grid {
    grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
    gap: 10px;
    padding: 10px 4px 80px; /* 底部留白避开 FAB / 操作条 */
  }
  .cell-thumb {
    height: 68px;
  }
  /* 传输列表：右下角浮窗 → 贴底通栏 */
  .fm-tasks {
    left: 8px;
    right: 8px;
    bottom: 8px;
    width: auto;
  }
}
</style>
