// 公开正文只作 Markdown 展示，不执行 HTML、viz 或图片请求。
import MarkdownIt from 'markdown-it'
import hljs from '../hljs.js'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  highlight(code, language) {
    if (language && hljs.getLanguage(language)) {
      return hljs.highlight(code, { language, ignoreIllegals: true }).value
    }
    return ''
  },
})
md.renderer.rules.image = (tokens, index) =>
  `<span class="md-image">[图片：${md.utils.escapeHtml(tokens[index].content || '未加载')}]</span>`
const linkOpen =
  md.renderer.rules.link_open ||
  ((tokens, index, options, env, self) => self.renderToken(tokens, index, options))
md.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index].attrSet('rel', 'noopener noreferrer nofollow')
  tokens[index].attrSet('target', '_blank')
  return linkOpen(tokens, index, options, env, self)
}
export const renderMarkdown = (source) => md.render(source || '')
export const markdownBytes = (source) => new TextEncoder().encode(source).length
export function validateMarkdown(source, maxBytes) {
  if (!source.trim()) throw new Error('请输入 Markdown 正文')
  // eslint-disable-next-line no-control-regex -- 拒绝二进制正文。
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(source))
    throw new Error('正文不能含二进制控制字符')
  if (markdownBytes(source) > maxBytes) throw new Error('正文超过大小限制')
}
export async function importMarkdown(file, maxBytes) {
  if (!/\.md$/i.test(file.name)) throw new Error('只支持 .md 文件')
  if (file.size > maxBytes) throw new Error('文件超过大小限制')
  let source
  try {
    source = new TextDecoder('utf-8', { fatal: true }).decode(await file.arrayBuffer())
  } catch {
    throw new Error('文件必须是 UTF-8 Markdown')
  }
  validateMarkdown(source, maxBytes)
  return source
}
