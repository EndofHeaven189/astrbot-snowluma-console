// 与后端 Pydantic/dataclass 结构对应的最小类型集合

// 字段名以后端为准（FIX-E）：后端返回 status / group / required，不是 state / route。
export interface CheckItem {
  id: string
  label: string
  group: 'common' | 'uv' | 'docker' | 'snowluma' | string
  scope: 'common' | 'uv' | 'docker' | 'snowluma' | string
  status: 'ok' | 'warn' | 'fail' | 'pending' | string
  value?: string | number | null
  detail?: string | null
  hint?: string | null
  required?: boolean
}

// 检查时间字段是 checked_at（FIX-BE），不是 detect_at。
export interface DetectResult {
  route: string | null
  checked_at: string
  duration_ms: number
  cached: boolean
  checks: CheckItem[]
  summary: { ok: number; warn: number; fail: number; pending: number; total?: number }
  blocking: string[]
  can_install: boolean
  root: string
  app_version: string
  endpoints?: Record<string, string>
}

export interface CaptureStatus {
  service: string
  password_status: 'captured' | 'unknown' | 'user_managed' | string
  password_source?: string | null
  captured_at?: string | null
  has_password: boolean
  job_state?: string | null
  job_started_at?: string | null
  job_window?: number | null
}

export interface OrphanEntry {
  service: string
  pid: number
  cmd?: string | null
  cwd?: string | null
  port?: number | null
  mode?: string | null
  started_at?: string | null
}

export interface BridgeCandidate {
  strategy: string
  host: string
  port: number
  url: string
  label?: string
  reachable?: boolean | null
}

export interface BridgeEndpoint {
  route: string
  host?: string | null
  port?: number | null
  url?: string | null
  strategy?: string
  dial_from?: string
  network_mode?: string
  candidates?: BridgeCandidate[]
  note?: string | null
  reachable?: boolean | null
  reason?: string | null
  error?: string
  /** Docker 路线容器内探测结果（FIX-AH 会同时提升到顶层） */
  resolved?: Record<string, unknown>
}

export interface ServiceStatus {
  name: string
  installed: boolean
  running: boolean
  version?: string | null
  deploy_mode?: string | null
  pid?: number | null
  port?: number | null
  url?: string | null
  state?: string
  detail?: Record<string, unknown>
  error?: string
}

export interface JobStep {
  name: string
  state: string
  detail?: string
  at: string
}

export interface InstallJob {
  id: string
  kind: string
  title: string
  state: 'running' | 'done' | 'failed' | string
  started_at: string
  finished_at?: string | null
  elapsed: number
  ok?: boolean | null
  error?: string | null
  steps: JobStep[]
  result: Record<string, unknown>
}

export interface EulaStatus {
  eula: string | null
  privacy: string | null
  accepted: boolean
  accepted_at: string | null
  version: string | null
  mode: string | null
  actor: string | null
  path?: string
  env_flags?: Record<string, string>
}

export interface ConfigFileMeta {
  id: string
  label: string
  service: string
  path: string
  exists: boolean
  size_mb: number
  modified_at?: string | null
}

export interface BackupEntry {
  name: string
  path: string
  size_mb?: number
  modified_at?: string | null
  original?: string
  reason?: string
  service?: string
}

export interface BridgeSide {
  exists: boolean
  path: string
  configured: boolean
  enabled: boolean
  port?: number | null
  host?: string | null
  url?: string | null
  token_set: boolean
  detail?: Record<string, unknown>
}

export interface BridgeStatus {
  onebot_port: number
  token_set: boolean
  astrbot: BridgeSide
  snowluma: BridgeSide
  consistent: boolean
  deploy_mode: string | null
  hint: string | null
  checked_at: string
  endpoint?: BridgeEndpoint | null
}

export interface SettingsShape {
  version: number
  console: { host: string; port: number }
  mirror: { enabled: boolean; pypi?: string; github?: string; docker?: string }
  astrbot: {
    deploy_mode: 'uv' | 'docker' | null
    dashboard_port: number
    onebot_port: number
    uv_strategy?: string
    project_dir?: string
    version?: string | null
    docker_network_mode?: 'bridge' | 'host'
  }
  snowluma: {
    webui_port: number
    http_port: number
    ws_port: number
    variant?: string
  }
  bridge: { onebot_port: number; token: string }
  astrbot_extra?: unknown
  timeouts?: Record<string, number>
  lifecycle?: { shutdown_child_processes: boolean }
}

export interface ApiError {
  code: string
  message: string
  hint?: string | null
  detail?: Record<string, unknown>
}
