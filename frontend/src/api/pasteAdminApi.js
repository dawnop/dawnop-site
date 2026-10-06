import client from './client'
const base = '/pastes/admin'
export const list = async (params) => (await client.get(base, { params })).data
export const detail = async (id) => (await client.get(`${base}/${encodeURIComponent(id)}`)).data
export const stats = async () => (await client.get(`${base}/stats`)).data
export const settings = async () => (await client.get(`${base}/settings`)).data
export const saveSettings = async (body) => (await client.put(`${base}/settings`, body)).data
export const remove = async (ids) => (await client.post(`${base}/delete`, { ids })).data
export const cleanup = async () => (await client.post(`${base}/cleanup`)).data
export const retain = async (id) =>
  (await client.post(`${base}/${encodeURIComponent(id)}/retain`)).data
