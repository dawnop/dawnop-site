// 匿名实例不继承后台凭据，也不触发后台登录跳转。
import axios from 'axios'
const client = axios.create({ baseURL: '/api/pastes', timeout: 20000 })
export const listPastes = async (params) => (await client.get('', { params })).data
export const getPaste = async (id) => (await client.get(`/${encodeURIComponent(id)}`)).data
export const createPaste = async (body) => (await client.post('', body)).data
export const getConfig = async () => (await client.get('/config')).data
export function errorText(error) {
  const detail = error?.response?.data?.detail
  return typeof detail === 'string' ? detail : '请求失败，请稍后重试'
}
