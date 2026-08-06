// 触发浏览器「另存为」：把内存里的 Blob 存成本地文件。
// 临时 <a> 必须先入文档再点，Firefox 不认游离节点；对象 URL 延后回收，
// 立刻 revoke 在部分浏览器会把还没开始写盘的下载掐掉。
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}
