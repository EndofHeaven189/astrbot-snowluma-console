<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, HttpError } from '../api/client'
import { isBusy, rawState, reportError, setBusy, toast } from '../store'
import StatusBadge from './StatusBadge.vue'
import type { ServiceStatus } from '../api/types'

const props = defineProps<{
  name: string
  title: string
  subtitle?: string
  icon?: string
}>()

const state = rawState
const busyKey = computed(() => `svc:${props.name}`)
const svc = computed<ServiceStatus | undefined>(() => state.services.find((s) => s.name === props.name))

const running = computed(() => Boolean(svc.value?.running))
const installed = computed(() => Boolean(svc.value?.installed))
const stateLabel = computed(() => (running.value ? 'running' : 'stopped'))

const loginUrl = ref<string | null>(null)
const failure = ref<Record<string, any> | null>(null)
const cleaning = ref(false)

const residualPids = computed<number[]>(() => {
  const d = (svc.value?.detail || {}) as Record<string, any>
  const ids = d.residual_pids
  return Array.isArray(ids) ? ids.map((x: any) => Number(x)) : []
})

const detail = computed(() => (svc.value?.detail || {}) as Record<string, any>)

async function act(kind: 'start' | 'stop' | 'restart', acknowledge = false) {
  setBusy(busyKey.value, true)
  failure.value = null
  try {
    const runner =
      kind === 'start'
        ? api.startService(props.name, true, acknowledge)
        : kind === 'stop'
          ? api.stopService(props.name)
          : api.restartServiceAck(props.name, acknowledge)
    const res = (await runner) as any
    if (res?.login_url) loginUrl.value = res.login_url
    toast.ok(`${props.title} ${kind === 'start' ? '已启动' : kind === 'stop' ? '已停止' : '已重启'}`)
    await refresh()
  } catch (err) {
    // FIX-05：SnowLuma 历史数据目录 → 让用户明确选择
    if (err instanceof HttpError && err.code === 'snowluma_history') {
      const files = ((err.detail?.files as string[]) || []).join('、')
      const ok = window.confirm(
        `${err.message}\n\n检测到的文件：${files || '—'}\n\n` +
          'SnowLuma 在历史数据目录上不会重新生成一次性初始密码，控制台也抓不到，只能用你原来设过的密码。\n\n' +
          '确认「已知道密码 / 会自行在面板里重置」，继续' +
          (kind === 'start' ? '启动' : '重启') +
          '吗？',
      )
      if (ok) await act(kind, true)
      return
    }
    failure.value = err instanceof HttpError ? { ...(err.detail || {}), message: err.message, hint: err.hint } : null
    reportError(`${props.title} 操作失败`, err)
  } finally {
    setBusy(busyKey.value, false)
  }
}

async function killResidual() {
  cleaning.value = true
  try {
    const res = await api.killResidual(props.name)
    toast.ok('残留进程已清理', `结束 PID：${(res.killed || res.result || []).join(', ') || '无'}`)
    await refresh()
  } catch (err) {
    reportError('清理残留进程失败', err)
  } finally {
    cleaning.value = false
  }
}

async function refresh() {
  try {
    const res = await api.serviceStatus()
    rawState.services = res.services
    rawState.deploy_mode = res.deploy_mode
    rawState.snowlumaLoginUrl = res.snowluma_login_url
  } catch {
    /* 忽略 */
  }
}

const openPanel = () => {
  // FIX-BL：优先带 token 的免密链接，再退回服务地址。
  const url = loginUrl.value || rawState.snowlumaLoginUrl || svc.value?.url
  if (url) window.open(url, '_blank', 'noopener')
}

const reasonText: Record<string, string> = {
  port_conflict: '端口已被别的程序占用',
  exited_before_ready: '进程在就绪前就退出了',
  ready_timeout: '等待就绪超时',
}
</script>

<template>
  <div class="card">
    <div class="card-head">
      <span style="font-size: 16px">{{ icon || '⚙️' }}</span>
      <div>
        <h2>{{ title }}</h2>
        <div v-if="subtitle" class="dim" style="font-size: 11.5px">{{ subtitle }}</div>
      </div>
      <div class="card-actions">
        <StatusBadge :state="stateLabel" />
      </div>
    </div>

    <dl class="kv">
      <dt>版本</dt>
      <dd>{{ svc?.version || '未安装' }}</dd>
      <dt>部署形态</dt>
      <dd>{{ svc?.deploy_mode || '—' }}</dd>
      <dt>端口</dt>
      <dd class="mono">
        {{ svc?.port ?? '—' }}
        <span v-if="detail.port_owner_pid" class="dim">（被 PID {{ detail.port_owner_pid }} 占用）</span>
      </dd>
      <dt>PID</dt>
      <dd class="mono">{{ svc?.pid ?? '—' }}</dd>
      <dt>访问地址</dt>
      <dd class="mono">
        <a v-if="svc?.url" :href="svc.url" target="_blank" rel="noopener">{{ svc.url }}</a>
        <span v-else>—</span>
      </dd>
    </dl>

    <div class="row wrap" style="margin-top: 14px">
      <button class="btn btn-primary btn-sm" :disabled="isBusy(busyKey) || running || !installed" @click="act('start')">
        启动
      </button>
      <button class="btn btn-sm" :disabled="isBusy(busyKey) || !running" @click="act('stop')">停止</button>
      <button class="btn btn-sm" :disabled="isBusy(busyKey) || !installed" @click="act('restart')">重启</button>
      <button class="btn btn-ghost btn-sm right" :disabled="!running" @click="openPanel">打开面板</button>
    </div>

    <div v-if="!installed" class="notice notice-warn" style="margin-top: 12px">
      尚未安装。请到「安装」页完成部署后再启动。
    </div>

    <div v-if="failure" class="notice notice-error" style="margin-top: 12px">
      <b>{{ reasonText[String(failure.reason)] || '启动失败' }}</b>：{{ failure.message }}
      <div v-if="failure.exit_code !== undefined && failure.exit_code !== null" class="dim" style="font-size: 12px">
        退出码 {{ failure.exit_code }}
      </div>
      <div v-if="failure.hint" class="dim" style="font-size: 12px; margin-top: 6px">{{ failure.hint }}</div>
      <details v-if="(failure.logs || []).length" style="margin-top: 8px">
        <summary class="dim" style="font-size: 12px; cursor: pointer">查看最后 {{ (failure.logs || []).length }} 行日志</summary>
        <pre class="mono" style="max-height: 220px; overflow: auto; font-size: 11.5px; margin: 8px 0 0">{{ (failure.logs || []).join('\n') }}</pre>
      </details>
    </div>

    <div v-if="residualPids.length" class="notice notice-error" style="margin-top: 12px">
      端口 {{ svc?.port }} 仍被占用，可能存在残留进程：<span class="mono">{{ residualPids.join(', ') }}</span>
      <div class="row" style="margin-top: 8px">
        <button class="btn btn-danger btn-sm" :disabled="cleaning" @click="killResidual">
          {{ cleaning ? '清理中…' : '一键清理残留' }}
        </button>
      </div>
    </div>

    <div v-else-if="svc?.error" class="notice notice-error" style="margin-top: 12px">
      {{ svc.error }}
    </div>
  </div>
</template>
