import type {
  ApiError,
  BackupEntry,
  BridgeEndpoint,
  BridgeStatus,
  CaptureStatus,
  OrphanEntry,
  ConfigFileMeta,
  DetectResult,
  EulaStatus,
  InstallJob,
  ServiceStatus,
  SettingsShape,
} from './types'

const BASE = ''

export class HttpError extends Error {
  status: number
  code: string
  hint?: string | null
  detail?: Record<string, unknown>

  constructor(status: number, payload: Partial<ApiError>) {
    super(payload.message || `请求失败（${status}）`)
    this.status = status
    this.code = payload.code || 'http_error'
    this.hint = payload.hint
    this.detail = payload.detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(BASE + path, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (err) {
    throw new HttpError(0, { message: '无法连接控制台后端，请确认 python main.py 正在运行' })
  }
  const text = await resp.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }
  if (!resp.ok) {
    throw new HttpError(resp.status, (payload as Partial<ApiError>) || { message: text.slice(0, 300) })
  }
  return payload as T
}

const get = <T>(path: string) => request<T>(path)
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
const put = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) })
const del = <T>(path: string) => request<T>(path, { method: 'DELETE' })

export const api = {
  meta: () => get<Record<string, any>>('/api/meta'),
  health: () => get<{ ok: boolean }>('/api/health'),

  detect: (force = false) => get<DetectResult>(`/api/env/detect?force=${force ? 'true' : 'false'}`),
  installDeps: (items: string[]) => post<Record<string, any>>('/api/env/install', { items }),

  installAstrbot: (mode: 'uv' | 'docker', extra: Record<string, unknown> = {}) =>
    post<{ job: InstallJob; mode: string }>('/api/install/astrbot', { mode, ...extra }),
  switchMode: (mode: 'uv' | 'docker') =>
    post<Record<string, any>>('/api/install/astrbot/switch-mode', { mode }),
  installSnowluma: (payload: {
    variant: 'lite' | 'full'
    accept_eula: boolean
    accept_privacy: boolean
    mode?: 'checkbox' | 'manual'
    force?: boolean
    acknowledge_history?: boolean
    fresh_data?: boolean
  }) =>
    post<{ job: InstallJob; variant: string; archived_config?: string | null }>(
      '/api/install/snowluma',
      payload,
    ),
  installState: () => get<Record<string, any>>('/api/install/state'),
  jobs: () => get<{ active: InstallJob | null; jobs: InstallJob[] }>('/api/install/jobs'),
  job: (id: string) => get<InstallJob>(`/api/install/jobs/${id}`),

  serviceStatus: () =>
    get<{
      services: ServiceStatus[]
      install_job: InstallJob | null
      snowluma_login_url: string | null
      deploy_mode: string | null
    }>('/api/service/status'),
  startService: (name: string, wait = false, acknowledge = false) =>
    post<Record<string, any>>(
      `/api/service/${name}/start?wait=${wait}&acknowledge=${acknowledge}`,
    ),
  restartServiceAck: (name: string, acknowledge = false) =>
    post<Record<string, any>>(`/api/service/${name}/restart?wait=true&acknowledge=${acknowledge}`),
  killResidual: (name: string) =>
    post<Record<string, any>>(`/api/service/${name}/kill-residual`),
  orphans: () =>
    get<{ orphans: Record<string, OrphanEntry>; count: number; hint?: string | null }>(
      '/api/service/orphans',
    ),
  scanOrphans: () =>
    post<{ orphans: Record<string, OrphanEntry>; count: number }>('/api/service/orphans/scan'),
  resolveOrphan: (name: string, action: 'kill' | 'adopt') =>
    post<Record<string, any>>(`/api/service/orphans/${name}/resolve`, { action }),
  stopService: (name: string) => post<Record<string, any>>(`/api/service/${name}/stop`),
  restartService: (name: string) => post<Record<string, any>>(`/api/service/${name}/restart?wait=true`),
  recentLog: (name: string, tail = 400) =>
    get<{ service: string; lines: string[]; file: string | null }>(
      `/api/service/${name}/log/recent?tail=${tail}`,
    ),
  logUrl: (name: string, tail = 200) => `/api/service/${name}/log?tail=${tail}`,

  configFiles: (service: string) =>
    get<{ service: string; files: ConfigFileMeta[] }>(`/api/config/${service}`),
  configRead: (service: string, id: string) =>
    get<Record<string, any>>(`/api/config/${service}/${id}`),
  configWrite: (service: string, id: string, data: unknown) =>
    put<Record<string, any>>(`/api/config/${service}/${id}`, { data, reason: 'manual' }),
  backups: (service: string) =>
    get<{ service: string; backups: BackupEntry[] }>(`/api/config/${service}/backups/list`),
  restoreBackup: (name: string) => post<Record<string, any>>('/api/config/backups/restore', { name }),

  credential: (service: string) => get<Record<string, any>>(`/api/credential/${service}`),
  saveCredential: (service: string, values: Record<string, unknown>) =>
    put<Record<string, any>>(`/api/credential/${service}`, { values }),
  resetCredential: (service: string) =>
    post<Record<string, any>>(`/api/credential/${service}/reset?confirm=true`),
  captureStatus: (service: string) =>
    get<CaptureStatus & { capture?: CaptureStatus }>(`/api/credential/${service}`),
  recaptureCredential: (service: string) =>
    post<Record<string, any>>(`/api/credential/${service}/recapture`),
  manualCredential: (service: string, password: string, note = '') =>
    post<Record<string, any>>(`/api/credential/${service}/manual`, { password, note }),

  bridgeStatus: () => get<BridgeStatus>('/api/bridge/status'),
  bridgeApply: (payload: { token?: string; onebot_port?: number }) =>
    post<Record<string, any>>('/api/bridge/apply', payload),
  bridgeVerify: () => post<Record<string, any>>('/api/bridge/verify'),
  bridgeEndpoint: (refresh = false) =>
    get<{ endpoint: BridgeEndpoint }>(`/api/bridge/endpoint?refresh=${refresh}`),
  bridgeFallback: () => post<{ endpoint: BridgeEndpoint; compose?: string }>('/api/bridge/fallback', { mode: 'host' }),

  settings: () =>
    get<{ settings: SettingsShape; file: string; root: string; eula: EulaStatus }>('/api/settings'),
  saveSettings: (patch: Record<string, unknown>) =>
    put<{ settings: SettingsShape; saved: boolean }>('/api/settings', patch),
  networkMode: (mode: 'bridge' | 'host') =>
    post<{ network_mode: string; compose?: string; regenerated: boolean; hint?: string }>(
      '/api/settings/network-mode',
      { mode },
    ),
  eula: () => get<EulaStatus>('/api/settings/eula'),
  acceptEula: (payload: { version?: string; mode?: string; eula?: boolean; privacy?: boolean }) =>
    post<Record<string, any>>('/api/settings/eula', payload),
  revokeEula: () => del<Record<string, any>>('/api/settings/eula'),
  changeRoot: (root: string) => post<Record<string, any>>('/api/settings/root', { root }),
}

export const errorText = (err: unknown): string => {
  if (err instanceof HttpError) {
    return err.hint ? `${err.message}（${err.hint}）` : err.message
  }
  return err instanceof Error ? err.message : String(err)
}
