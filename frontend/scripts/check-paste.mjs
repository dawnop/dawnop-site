// 匿名 Markdown 的可执行内容、文件类型与字节边界必须被拒绝。
import assert from 'node:assert/strict'
import {
  renderMarkdown,
  markdownBytes,
  validateMarkdown,
  importMarkdown,
} from '../src/utils/safeMarkdown.js'
const rendered = renderMarkdown(
  '<script>alert(1)</script>\n\n![追踪](https://tracker.invalid/a.png)\n\n```viz\nalert(2)\n```\n\n[危险](javascript:alert(3))\n\n[链接](https://example.com)',
)
assert(!rendered.includes('<script>'))
assert(!rendered.includes('<img'))
assert(!rendered.includes('href="javascript:'))
assert(rendered.includes('language-viz'))
assert(rendered.includes('noopener noreferrer nofollow'))
assert(rendered.includes('&lt;script&gt;'))
assert.equal(markdownBytes('你好😀'), 10)
assert.doesNotThrow(() => validateMarkdown('你好', 6))
assert.throws(() => validateMarkdown('你好a', 6))
assert.throws(() => validateMarkdown('a\x00b', 100))
assert.throws(() => validateMarkdown(' \n', 100))
const file = (name, bytes) => ({
  name,
  size: bytes.length,
  arrayBuffer: async () => Uint8Array.from(bytes).buffer,
})
assert.equal(await importMarkdown(file('你好.MD', Buffer.from('# 你好')), 100), '# 你好')
await assert.rejects(importMarkdown(file('note.txt', Buffer.from('正文')), 100), /只支持/)
await assert.rejects(importMarkdown(file('note.markdown', Buffer.from('正文')), 100), /只支持/)
await assert.rejects(importMarkdown(file('note.md', [255]), 100), /UTF-8/)
await assert.rejects(importMarkdown(file('note.md', Buffer.from('你好')), 5), /超过/)
console.log(
  'PASS: Markdown HTML/viz/image/link isolation, .md import, strict UTF-8 and byte boundaries',
)
