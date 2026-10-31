<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, HttpError } from '../api/client'
import { rawState, reloadSettings, reportError, toast } from '../store'
import type { InstallJob, JobStep } from '../api/types'

const state = rawState

const installState = ref<Record<string, any> | null>(null)
const job = ref<InstallJob | null>(null)
const history = ref<InstallJob[]>([])
let timer: number | null = null

const mode = ref<'uv' | 'docker'>('uv')
const upgrade = ref(false)
const switching = ref(false)
const starting = ref(false)

const variant = ref<'lite' | 'full'>('lite')
const acceptEula = ref(false)
const acceptPrivacy = ref(false)
const manualMode = ref(false)
const slStarting = ref(false)
const slHistory = ref<Record<string, any> | null>(null)
const historyModal = ref<Record<string, any> | null>(null)

const current = computed(() => state.settings?.astrbot?.deploy_mode ?? null)
const changed = computed(() => current.value !== null && current.value !== mode.value)
const busy = computed(() => job.value?.state === 'running')

const canInstall = computed(() => {
  if (busy.value) return false
  return true
})

const slUnlocked = computed(() => manualMode.value || (acceptEula.value && acceptPrivacy.value))
const slInstalled = computed(() => Boolean(installState.value?.snowluma?.installed))

function syncMode() {
  if (current.value === 'uv' || current.value === 'docker') mode.value = current.value
}

async function loadSlHistory() {
  try {
    const r = await fetch('/api/config/snowluma/history').then((x) => x.json())
    slHistory.value = r
  } catch {
    slHistory.value = null
  }
}

async function loadState() {
  try {
    installState.value = await api.installState()
    await loadSlHistory()
    syncMode()
    const jobs = await api.jobs()
    history.value = jobs.jobs
    if (jobs.active) job.value = jobs.active
    else if (job.value && job.value.state === 'running') {
      job.value = await api.job(job.value.id)
    }
    schedulePoll()
  } catch (err) {
    /* 后端可能刚起来 */
  }
}

function schedulePoll() {
  if (timer !== null) window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    if (job.value?.state === 'running') {
      try {
        job.value = await api.job(job.value.id)
      } catch {
        /* 忽略 */
      }
      if (job.value?.state === 'running') schedulePoll()
      else {
        await loadState()
        await reloadSettings()
        if (job.value?.state === 'done') toast.ok('任务完成', job.value.title)
        else if (job.value?.state === 'failed') toast.error('任务失败', job.value.error || '')
      }
    }
  }, 1200)
}

async function doInstall() {
  starting.value = true
  try {
    const res = await api.installAstrbot(mode.value, {
      upgrade: upgrade.value,
      dashboard_port: state.settings?.astrbot?.dashboard_port,
      onebot_port: state.settings?.astrbot?.onebot_port,
    })
    job.value = res.job
    toast.info('已开始安装 AstrBot', mode.value === 'docker' ? 'Docker 路线' : 'uv 路线')
    schedulePoll()
  } catch (err) {
    reportError('启动安装失败', err)
  } finally {
    starting.value = false
  }
}

async function doSwitch() {
  if (!window.confirm(`切换部署形态到 ${mode.value === 'docker' ? 'Docker' : 'uv/pip'}？\n\n会先停止当前正在运行的 AstrBot；instances 目录不会被覆盖。`)) return
  switching.value = true
  try {
    const res = await api.switchMode(mode.value)
    await reloadSettings()
    await loadState()
    if (res.changed) toast.ok('已切换部署形态', res.hint || '')
    else toast.info('未变化', res.hint || '')
    if (res.needs_install) toast.info('需要重新安装', '新路线尚未安装对应运行时')
  } catch (err) {
    reportError('切换失败', err)
  } finally {
    switching.value = false
  }
}

async function doInstallSnowluma(force = false, extra: Record<string, unknown> = {}) {
  slStarting.value = true
  try {
    const res = await api.installSnowluma({
      variant: variant.value,
      accept_eula: acceptEula.value || manualMode.value,
      accept_privacy: acceptPrivacy.value || manualMode.value,
      mode: manualMode.value ? 'manual' : 'checkbox',
      force,
      ...extra,
    })
    job.value = res.job
    historyModal.value = null
    if (res.archived_config) toast.ok('旧配置已归档', String(res.archived_config))
    toast.info('已开始安装 SnowLuma', variant.value)
    await loadSlHistory()
    schedulePoll()
  } catch (err) {
    // FIX-05：历史数据目录 → 前置弹窗，给出两条出路
    if (err instanceof HttpError && err.code === 'snowluma_history') {
      historyModal.value = {
        message: err.message,
        hint: err.hint,
        ...(err.detail || {}),
      }
      return
    }
    reportError('启动安装失败', err)
  } finally {
    slStarting.value = false
  }
}

async function archiveAndInstall() {
  if (
    !window.confirm(
      '把当前 instances\\SnowLuma\\config 整体移到 backups 下，然后用全新数据目录安装？\n\n' +
        '旧配置会保留在 backups 里，需要时可以手工拷回。',
    )
  )
    return
  await doInstallSnowluma(false, { fresh_data: true })
}

async function acknowledgeAndInstall() {
  await doInstallSnowluma(false, { acknowledge_history: true })
}

async function acceptManually() {
  manualMode.value = true
  try {
    await api.acceptEula({ mode: 'manual', eula: true, privacy: true })
    await reloadSettings()
    toast.ok('已记录人工确认', '已写入 eula_acceptance.json（含时间戳）')
  } catch (err) {
    reportError('记录人工确认失败', err)
  }
}

const stepCls = (s: JobStep) => {
  if (s.state === 'done' || s.state === 'ok') return 'badge badge-ok'
  if (s.state === 'failed' || s.state === 'error') return 'badge badge-error'
  if (s.state === 'running') return 'badge badge-running breathe'
  return 'badge badge-idle'
}

onMounted(loadState)
onBeforeUnmount(() => {
  if (timer !== null) window.clearTimeout(timer)
})
</script>

<template>
  <div class="stack-lg">
    <!-- AstrBot -->
    <div class="card">
      <div class="card-head">
        <span>🤖</span>
        <h2>AstrBot 安装</h2>
        <div class="card-actions">
          <StatusBadge
            :state="current ? 'ok' : 'warn'"
            :label="current ? (current === 'docker' ? '当前路线：Docker' : '当前路线：uv / pip') : '尚未选择路线'"
          />
        </div>
      </div>

      <div class="grid cols-2" style="margin-bottom: 14px">
        <div
          class="card choice"
          :class="{ selected: mode === 'uv' }"
          style="cursor: pointer"
          @click="mode = 'uv'"
        >
          <div class="row" style="gap: 8px">
            <b>uv / pip 原生</b>
            <span class="badge badge-ok">推荐</span>
          </div>
          <p class="dim" style="font-size: 12.5px; margin: 8px 0 0">
            在 <span class="mono">instances\AstrBot\venv</span> 里装 <span class="mono">astrbot</span>（uv venv + uv pip），
            用 subprocess 拉起，无 Docker 依赖。删掉 <span class="mono">apps\</span> 重装不会影响这个 venv 与数据。
          </p>
        </div>
        <div
          class="card choice"
          :class="{ selected: mode === 'docker' }"
          style="cursor: pointer"
          @click="mode = 'docker'"
        >
          <div class="row" style="gap: 8px">
            <b>Docker 容器</b>
            <span class="badge badge-idle">需 Docker Desktop</span>
          </div>
          <p class="dim" style="font-size: 12.5px; margin: 8px 0 0">
            使用 <span class="mono">docker compose up -d</span> 跑 <span class="mono">soulter/astrbot:latest</span>。
            注意 WSL2 端口映射可能影响与 SnowLuma 的反向 WS 连通。
          </p>
        </div>
      </div>

      <div class="row wrap">
        <label class="check" style="font-size: 12.5px">
          <input v-model="upgrade" type="checkbox" />强制升级到最新版本
        </label>
        <div class="right row">
          <button v-if="changed" class="btn btn-sm" :disabled="switching || busy" @click="doSwitch">
            {{ switching ? '切换中…' : `切换路线到 ${mode === 'docker' ? 'Docker' : 'uv'}` }}
          </button>
          <button class="btn btn-primary btn-sm" :disabled="!canInstall || starting" @click="doInstall">
            {{ starting ? '提交中…' : changed ? '按新路线安装' : '开始安装 / 更新' }}
          </button>
        </div>
      </div>

      <div v-if="changed" class="notice notice-warn" style="margin-top: 12px">
        选择的路线与当前记录不一致。切换会先停止正在运行的 AstrBot，<b>instances 目录不会被动</b>，但新路线需要重新安装运行时。
      </div>

      <div v-if="installState?.astrbot" class="kv-block" style="margin-top: 14px">
        <dl class="kv">
          <dt>uv 是否已装</dt>
          <dd>{{ installState.astrbot.uv_installed ? '是' : '否' }}</dd>
          <dt>AstrBot 版本</dt>
          <dd class="mono">{{ state.settings?.astrbot?.version || '未记录' }}</dd>
          <dt>实例目录</dt>
          <dd class="mono">{{ installState.astrbot.instance_dir }}</dd>
          <dt>数据目录</dt>
          <dd class="mono">{{ installState.astrbot.data_dir }}</dd>
          <dt>compose 文件</dt>
          <dd class="mono">{{ installState.astrbot.compose_file }}</dd>
        </dl>
      </div>
    </div>

    <!-- SnowLuma -->
    <div class="card">
      <div class="card-head">
        <span>❄️</span>
        <h2>SnowLuma 安装</h2>
        <div class="card-actions">
          <StatusBadge :state="slInstalled ? 'ok' : 'warn'" :label="slInstalled ? '已安装' : '未安装'" />
        </div>
      </div>

      <div class="row wrap" style="gap: 16px; margin-bottom: 12px">
        <label class="check">
          <input v-model="variant" type="radio" value="lite" />lite（约 7 MB，需本机 Node ≥ 24.22.1）
        </label>
        <label class="check">
          <input v-model="variant" type="radio" value="full" />full（约 43 MB，自带运行时）
        </label>
      </div>

      <p class="dim" style="font-size: 12.5px; margin: 0 0 12px">
        安装器会下载官方 zip 解压到 <span class="mono">apps\SnowLuma</span>，并在启动时注入
        <span class="mono">SNOWLUMA_ACCEPT_EULA=1</span> 与 <span class="mono">SNOWLUMA_ACCEPT_PRIVACY=1</span>（必须同时设置）。
      </p>

      <div class="stack" style="gap: 8px">
        <label class="check" :class="{ disabled: manualMode }">
          <input v-model="acceptEula" type="checkbox" :disabled="manualMode" />
          我已阅读并同意 <b>SnowLuma 最终用户许可协议（EULA）</b>
        </label>
        <label class="check" :class="{ disabled: manualMode }">
          <input v-model="acceptPrivacy" type="checkbox" :disabled="manualMode" />
          我已阅读并同意 <b>SnowLuma 隐私政策</b>
        </label>
      </div>

      <div class="row wrap" style="margin-top: 14px">
        <button class="btn btn-primary btn-sm" :disabled="!slUnlocked || slStarting || busy" @click="doInstallSnowluma(false)">
          {{ slStarting ? '提交中…' : '安装 SnowLuma' }}
        </button>
        <button
          v-if="slInstalled"
          class="btn btn-ghost btn-sm"
          :disabled="!slUnlocked || busy"
          @click="doInstallSnowluma(true)"
        >
          重新下载覆盖安装
        </button>
        <button v-if="!manualMode" class="btn btn-ghost btn-sm right" @click="acceptManually">
          无法勾选？走人工确认
        </button>
      </div>

      <div v-if="!slUnlocked" class="notice notice-warn" style="margin-top: 12px">
        必须同时勾选 EULA 与隐私政策才能解锁安装按钮。若你在无图形环境下无法勾选，请点右上角「走人工确认」，控制台会以 manual 模式记录到
        <span class="mono">state\eula_acceptance.json</span>（含时间戳与版本号）。
      </div>
      <div v-else-if="manualMode" class="notice notice-warn" style="margin-top: 12px">
        已进入人工确认模式：勾选框被锁定，安装仍会按要求注入两个环境变量，并在 eula_acceptance.json 里记录 mode=manual。
      </div>

      <div v-if="slHistory?.history" class="notice notice-error" style="margin-top: 12px">
        <b>检测到 SnowLuma 历史数据目录</b>：<span class="mono">{{ (slHistory.files || []).join('、') }}</span>。
        历史目录不会重新生成一次性初始密码，控制台也就抓不到。
        安装前会再弹一次确认，你可以选择「新建全新数据目录」或「我已自行重置密码，继续」。
      </div>

      <div v-if="installState?.snowluma?.eula" class="dim" style="font-size: 12px; margin-top: 12px">
        最近一次协议记录：{{ installState.snowluma.eula.accepted ? '已接受' : '无记录' }}
        <span v-if="installState.snowluma.eula.accepted_at"> · {{ installState.snowluma.eula.accepted_at }}</span>
        <span v-if="installState.snowluma.eula.version"> · 版本 {{ installState.snowluma.eula.version }}</span>
      </div>
    </div>

    <!-- FIX-05 历史数据目录前置弹窗 -->
    <div v-if="historyModal" class="card" style="border-color: rgba(239, 68, 68, 0.55)">
      <div class="card-head">
        <span>⚠️</span>
        <h2>检测到 SnowLuma 历史数据目录</h2>
      </div>
      <p style="font-size: 13px; margin-top: 0">{{ historyModal.message }}</p>
      <dl class="kv">
        <dt>配置目录</dt>
        <dd class="mono">{{ historyModal.config_dir || '—' }}</dd>
        <dt>命中的文件</dt>
        <dd class="mono">{{ (historyModal.files || []).join('、') || '—' }}</dd>
        <dt>当前密码状态</dt>
        <dd>{{ historyModal.password_status || 'unknown' }}</dd>
      </dl>
      <div class="notice notice-warn" style="margin-top: 12px">
        请不要直接继续：在这份历史目录上 SnowLuma 不会重新生成初始密码，控制台永远抓不到。
        请二选一 ——
      </div>
      <div class="row wrap" style="margin-top: 14px">
        <button class="btn btn-primary btn-sm" @click="archiveAndInstall">① 新建全新数据目录（旧配置移入 backups）</button>
        <button class="btn btn-sm" @click="acknowledgeAndInstall">② 我已自行在面板里重置密码，继续</button>
        <button class="btn btn-ghost btn-sm right" @click="historyModal = null">取消</button>
      </div>
      <div v-if="historyModal.hint" class="dim" style="font-size: 12px; margin-top: 10px">{{ historyModal.hint }}</div>
    </div>

    <!-- 任务进度 -->
    <div v-if="job" class="card">
      <div class="card-head">
        <span>⏳</span>
        <h2>任务进度</h2>
        <div class="card-actions"><StatusBadge :state="job.state === 'done' ? 'ok' : job.state" /></div>
      </div>
      <div class="row wrap" style="margin-bottom: 10px">
        <span class="dim" style="font-size: 12px">{{ job.title }} · 开始 {{ job.started_at }} · {{ job.elapsed.toFixed(1) }}s</span>
      </div>
      <div class="progress" style="margin-bottom: 12px">
        <div class="progress-bar" :style="{ width: job.state === 'done' ? '100%' : job.steps.length ? Math.min(96, job.steps.filter((s) => s.state === 'ok' || s.state === 'skip').length * 12 + 12) + '%' : '8%' }" />
      </div>
      <div class="stack" style="gap: 6px">
        <div v-for="(s, i) in job.steps" :key="i" class="row" style="gap: 10px; align-items: flex-start">
          <span :class="stepCls(s)" style="flex: 0 0 auto">{{ s.state }}</span>
          <span style="font-size: 12.5px">{{ s.name }}</span>
          <span v-if="s.detail" class="dim mono" style="font-size: 11.5px; margin-left: auto; text-align: right">{{ s.detail }}</span>
        </div>
      </div>
      <div v-if="job.error" class="notice notice-error" style="margin-top: 12px">{{ job.error }}</div>
    </div>

    <div v-if="history.length" class="card">
      <div class="card-head">
        <span>📋</span>
        <h2>历史任务</h2>
        <div class="card-actions"><button class="btn btn-sm btn-ghost" @click="loadState">刷新</button></div>
      </div>
      <table class="table">
        <thead>
          <tr><th>任务</th><th style="width: 90px">状态</th><th style="width: 150px">开始</th><th style="width: 80px">耗时</th></tr>
        </thead>
        <tbody>
          <tr v-for="j in history" :key="j.id">
            <td>{{ j.title }}</td>
            <td><StatusBadge :state="j.state === 'done' ? 'ok' : j.state" /></td>
            <td class="mono" style="font-size: 11.5px">{{ j.started_at }}</td>
            <td>{{ j.elapsed.toFixed(1) }}s</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
