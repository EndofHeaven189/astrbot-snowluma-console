import { reactive, computed, readonly } from 'vue'
import { api, errorText } from './api/client'
import type { EulaStatus, ServiceStatus, SettingsShape } from './api/types'

export interface Toast {
  id: number
  title: string
  text?: string
  kind: 'ok' | 'error' | 'info'
}

interface State {
  ready: boolean
  meta: Record<string, any> | null
  settings: SettingsShape | null
  eula: EulaStatus | null
  services: ServiceStatus[]
  deploy_mode: string | null
  snowlumaLoginUrl: string | null
  toasts: Toast[]
  busy: Record<string, boolean>
  backendDown: boolean
}

const state = reactive<State>({
  ready: false,
  meta: null,
  settings: null,
  eula: null,
  services: [],
  deploy_mode: null,
  snowlumaLoginUrl: null,
  toasts: [],
  busy: {},
  backendDown: false,
})

let toastSeq = 0
let poller: number | null = null

function pushToast(kind: Toast['kind'], title: string, text?: string) {
  const toast: Toast = { id: ++toastSeq, title, text, kind }
  state.toasts.push(toast)
  window.setTimeout(() => {
    const idx = state.toasts.findIndex((t) => t.id === toast.id)
    if (idx >= 0) state.toasts.splice(idx, 1)
  }, kind === 'error' ? 7000 : 3600)
}

export const toast = {
  ok: (title: string, text?: string) => pushToast('ok', title, text),
  error: (title: string, text?: string) => pushToast('error', title, text),
  info: (title: string, text?: string) => pushToast('info', title, text),
}

export function reportError(title: string, err: unknown) {
  toast.error(title, errorText(err))
}

async function refreshStatus() {
  try {
    const res = await api.serviceStatus()
    state.services = res.services
    state.deploy_mode = res.deploy_mode
    state.snowlumaLoginUrl = res.snowluma_login_url
    state.backendDown = false
  } catch (err) {
    state.backendDown = true
  }
}

async function bootstrap() {
  try {
    const [meta, settings] = await Promise.all([api.meta(), api.settings()])
    state.meta = meta
    state.settings = settings.settings
    state.eula = settings.eula
    await refreshStatus()
    state.ready = true
    state.backendDown = false
  } catch (err) {
    state.backendDown = true
    state.ready = true
  }
}

function startPolling(intervalMs = 4000) {
  if (poller !== null) return
  poller = window.setInterval(() => {
    if (document.hidden) return
    void refreshStatus()
  }, intervalMs)
}

function stopPolling() {
  if (poller !== null) {
    window.clearInterval(poller)
    poller = null
  }
}

export async function reloadSettings() {
  const res = await api.settings()
  state.settings = res.settings
  state.eula = res.eula
  return res
}

export async function reloadEula() {
  try {
    state.eula = await api.eula()
  } catch {
    /* 忽略 */
  }
}

export const useStore = () => ({
  state: readonly(state) as State,
  bootstrap,
  refreshStatus,
  startPolling,
  stopPolling,
  reloadSettings,
  reloadEula,
})

export const serviceByName = computed(() =>
  // 便于模板里快速取状态
  Object.fromEntries(state.services.map((s) => [s.name, s])) as Record<string, ServiceStatus | undefined>,
)

export const rawState = state

export function setBusy(key: string, value: boolean) {
  if (value) state.busy[key] = true
  else delete state.busy[key]
}

export const isBusy = (key: string) => Boolean(state.busy[key])
