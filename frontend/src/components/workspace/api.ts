import { API_BASE_URL } from '../../lib/config';
export const apiBase = API_BASE_URL;
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(API_BASE_URL + path, init);
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : value.detail?.questions?.join('；') || '请求未能完成');
  return value as T;
}
export const json = (method: string, value: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
export const workspaceBase = '/api/workspace/projects';
export const researchBase = (pid: string) => `/api/research/projects/${pid}`;
