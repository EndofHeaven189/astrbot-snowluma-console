<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api, HttpError } from '../api/client'
import { reportError, toast } from '../store'
import type { CaptureStatus } from '../api/types'

const service = ref<'astrbot' | 'snowluma'>('astrbot')
const fields = ref<string[]>([])
const values = ref<Record<string, any>>({})
const draft = ref<Record<string, string>>({})
const reveal = ref(false)
const file = ref('')
const loginUrl = ref<string | null>(null)
const webuiUrl = ref<string | null>(null)
const capture = ref<CaptureStatus | null>(null)
const history = ref<Record<string, any> | null>(null)
const loading = ref(false)
const saving = ref(false)
const resetting = ref(false)
const recapturing = ref(false)
const manualValue = ref('')
const manualNote = ref('')

const dirty = computed(() =>
  Object.entries(draft.value).some(([k, v]) => String(values.value[k] ?? '') !== v),
)

const labels: Record<string, string> = {
  dashboard_username: '面板用户名',
  dashboard_password: '面板密码',
  password: '密码（兼容字段）',
  ws_token: 'OneBot 反向 WS Token',
  http_token: 'SnowLuma HTTP accessToken（端口 3000）',
  ws_token_snowluma: 'SnowLuma WS accessToken（端口 3001）',
  http_access_token: 'SnowLuma HTTP accessToken（端口 3000）',
  ws_access_token: 'SnowLuma WS accessToken（端口 3001）',
  access_token: 'accessToken',
  token: 'Token',
  initial_password: '一次性初始密码',
  onebot_token: 'OneBot Token',
  admin_password: 'SnowLuma 管理密码',
}

const labelOf = (k: string) => labels[k] || k

// password_status：captured / unknown / user_managed（FIX-04/05）
const statusOf = computed(() => capture.value?.password_status || 'unknown')
const statusBadge = computed(() => {
  if (statusOf.value === 'captured') return { state: 'ok', label: '已抓取' }
  if (statusOf.value === 'user_managed') return { state: 'ok', label: '用户维护' }
  return { state: 'warn', label: '未知' }
})
const statusText = computed(() => {
  if (statusOf.value === 'captured')
    return `密码已自动抓取（来源：${capture.value?.password_source || 'log'}，时间：${capture.value?.captured_at || '—'}）`
  if (statusOf.value === 'user_managed') return '密码由你手动填写，控制台不会用日志覆盖。'
  return '控制台还不知道密码：可能是历史数据目录，或密码在采集窗口开始前就已打印。可用下方三个补救入口。'
})

const canRenderToken = computed(() => Boolean(loginUrl.value) && statusOf.value !== 'unknown')

// FIX-Z / FIX-BJ：面板端口读设置，不再硬编码 6185。
const panelHost = computed(() => window.location.hostname || '127.0.0.1')
const dashboardUrl = computed(() => {
  const port = state.settings?.astrbot?.dashboard_port ?? 6185
  return `http://${panelHost.value}:${port}`
})

async function load() {
  loading.value = true
  try {
    const res = await api.credential(service.value)
    fields.value = res.fields || []
    values.value = res.values || {}
    file.value = res.file || ''
    loginUrl.value = res.login_url || null
    webuiUrl.value = res.webui_url || null
    capture.value = (res.capture as CaptureStatus) || null
    history.value = res.history || null
    const d: Record<string, string> = {}
    for (const k of fields.value) d[k] = values.value[k] == null ? '' : String(values.value[k])
    draft.value = d
  } catch (err) {
    reportError('读取凭据失败', err)
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const payload: Record<string, any> = {}
    for (const [k, v] of Object.entries(draft.value)) {
      if (String(values.value[k] ?? '') !== v) payload[k] = v
    }
    if (!Object.keys(payload).length) {
      toast.info('没有改动')
      return
    }
    await api.saveCredential(service.value, payload)
    toast.ok('凭据已保存', '文件权限已收紧为仅当前用户可读')
    await load()
  } catch (err) {
    reportError('保存失败', err)
  } finally {
    saving.value = false
  }
}

async function recapture() {
  recapturing.value = true
  try {
    const res = await api.recaptureCredential(service.value)
    // FIX-BM：后端返回体顶层没有 state，抓取进度在 status.job_state。
    const jobState = res.status?.job_state
    if (res.captured || jobState === 'captured') toast.ok('已重新抓取到密码', '已写入凭据库')
    else if (jobState === 'running') toast.info('后台正在扫日志', '窗口内命中会自动入库')
    else toast.error('暂未抓到密码', '可等待窗口结束，或改用手动粘贴')
    await load()
  } catch (err) {
    reportError('重新抓取失败', err)
  } finally {
    recapturing.value = false
  }
}

async function submitManual() {
  if (!manualValue.value.trim()) {
    toast.error('请先粘贴密码')
    return
  }
  try {
    await api.manualCredential(service.value, manualValue.value.trim(), manualNote.value.trim())
    toast.ok('已入库', '状态标记为用户维护，日志不会覆盖')
    manualValue.value = ''
    manualNote.value = ''
    await load()
  } catch (err) {
    reportError('手动入库失败', err)
  }
}

async function resetAstrbot() {
  if (
    !window.confirm(
      '重置 AstrBot 面板密码？\n\nuv 路线：在 venv 里执行 astrbot conf set dashboard.password 并重启进程；Docker 路线：docker exec + docker restart。',
    )
  )
    return
  resetting.value = true
  try {
    const res = await api.resetCredential('astrbot')
    toast.ok('已重置', res.password ? '新密码已写入凭据库' : res.message || '')
    await load()
  } catch (err) {
    reportError('重置失败', err)
  } finally {
    resetting.value = false
  }
}

async function freshDataDir() {
  if (
    !window.confirm(
      '把当前 instances\\SnowLuma\\config 整体挪到 backups 下，得到一个全新的数据目录？\n\n旧配置会保留在 backups 里，需要时可以手工拷回。',
    )
  )
    return
  try {
    const res = await fetch('/api/config/snowluma/archive-config', { method: 'POST' }).then(async (r) => {
      if (!r.ok) throw new HttpError(r.status, await r.json().catch(() => ({})))
      return r.json()
    })
    toast.ok('已归档旧配置', res.archived || '')
    await load()
  } catch (err) {
    reportError('归档失败', err)
  }
}

const masked = (v: string) => (reveal.value ? v : v ? '•'.repeat(Math.min(18, Math.max(6, v.length))) : '')

watch(service, load)
onMounted(load)
</script>

<template>
  <div class="stack-lg">
    <div class="card">
      <div class="card-head">
        <span>🔑</span>
        <h2>凭据库</h2>
        <div class="card-actions row">
          <StatusBadge :state="statusBadge.state" :label="'密码：' + statusBadge.label" />
          <label class="check" style="font-size: 12px"><input v-model="reveal" type="checkbox" />显示明文</label>
          <button class="btn btn-sm btn-ghost" @click="load">重新读取</button>
        </div>
      </div>

      <div class="row wrap" style="margin-bottom: 12px">
        <button class="btn btn-sm" :class="service === 'astrbot' ? 'btn-primary' : 'btn-ghost'" @click="service = 'astrbot'">
          AstrBot
        </button>
        <button class="btn btn-sm" :class="service === 'snowluma' ? 'btn-primary' : 'btn-ghost'" @click="service = 'snowluma'">
          SnowLuma
        </button>
      </div>

      <div class="notice notice-warn" style="margin-bottom: 12px">
        凭据以<b>明文</b>存放在 <span class="mono">{{ file || 'state\credentials.json' }}</span>，
        仅靠文件权限（仅当前用户可读写）保护。不要把这个目录提交到任何仓库。
      </div>

      <div class="notice" :class="statusOf === 'captured' || statusOf === 'user_managed' ? 'notice-ok' : 'notice-warn'" style="margin-bottom: 14px">
        {{ statusText }}
      </div>

      <!-- 三个补救入口（FIX-04） -->
      <div class="card" style="padding: 14px; margin-bottom: 14px">
        <div class="row wrap" style="gap: 10px">
          <button class="btn btn-sm" :disabled="recapturing" @click="recapture">
            {{ recapturing ? '抓取中…' : '① 重新抓取' }}
          </button>
          <span class="dim" style="font-size: 12px">
            从内存环形缓冲与日志里再扫一遍（窗口 {{ capture?.job_window || 180 }}s）
          </span>
        </div>
        <div class="row wrap" style="gap: 8px; margin-top: 10px; align-items: flex-end">
          <div class="field" style="flex: 1 1 220px">
            <label>② 手动粘贴密码</label>
            <input v-model="manualValue" type="text" class="mono" placeholder="面板里显示的密码" spellcheck="false" />
          </div>
          <div class="field" style="flex: 0 1 160px">
            <label>备注（可选）</label>
            <input v-model="manualNote" type="text" class="mono" placeholder="例如 面板内重置" />
          </div>
          <button class="btn btn-sm btn-primary" @click="submitManual">入库</button>
        </div>
        <div class="row wrap" style="gap: 10px; margin-top: 10px">
          <button v-if="service === 'astrbot'" class="btn btn-danger btn-sm" :disabled="resetting" @click="resetAstrbot">
            {{ resetting ? '重置中…' : '③ 走重置（AstrBot 面板密码）' }}
          </button>
          <span v-else class="dim" style="font-size: 12px">
            ③ SnowLuma 不支持控制台重置：请在 WebUI 的账号设置里改密码，再用上面的「手动粘贴」入库。
          </span>
        </div>
      </div>

      <div v-if="service === 'snowluma' && history?.history" class="notice notice-error" style="margin-bottom: 14px">
        <b>历史数据目录：</b>检测到 <span class="mono">{{ (history.files || []).join('、') }}</span>，
        SnowLuma 不会重新生成一次性初始密码，控制台也抓不到。
        <div class="row wrap" style="gap: 8px; margin-top: 8px">
          <button class="btn btn-sm" @click="freshDataDir">新建全新数据目录</button>
          <span class="dim" style="font-size: 12px">或直接在 SnowLuma 面板里重置密码，然后手动粘贴入库</span>
        </div>
      </div>

      <div v-if="!fields.length && !loading" class="empty">还没有采集到凭据。安装或启动服务后会自动写入。</div>

      <div class="stack" style="gap: 14px">
        <div v-for="k in fields" :key="k" class="field">
          <label>{{ labelOf(k) }}</label>
          <input
            v-model="draft[k]"
            :type="reveal ? 'text' : 'password'"
            class="mono"
            :placeholder="masked(String(values[k] ?? '')) || '未设置'"
            spellcheck="false"
          />
        </div>
      </div>

      <div class="row wrap" style="margin-top: 16px">
        <button class="btn btn-primary btn-sm" :disabled="!dirty || saving" @click="save">
          {{ saving ? '保存中…' : '保存凭据' }}
        </button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>ℹ️</span>
        <h2>登录入口</h2>
      </div>
      <dl class="kv">
        <dt>AstrBot 面板</dt>
        <dd class="mono">
          <a v-if="service === 'astrbot'" :href="dashboardUrl" target="_blank" rel="noopener">{{ dashboardUrl }}</a>
          <span v-else>—</span>
        </dd>
        <dt>SnowLuma WebUI</dt>
        <dd class="mono">
          <a v-if="canRenderToken && loginUrl" :href="loginUrl" target="_blank" rel="noopener">{{ loginUrl }}</a>
          <a v-else-if="service === 'snowluma' && webuiUrl" :href="webuiUrl" target="_blank" rel="noopener">{{ webuiUrl }}</a>
          <span v-else>—</span>
        </dd>
      </dl>
      <div class="dim" style="font-size: 12px; margin-top: 10px">
        SnowLuma 的免密登录链接形如 <span class="mono">{{ webuiUrl || '（WebUI 地址）' }}?token=&lt;一次性密码&gt;</span>。
        只有状态为「已抓取 / 用户维护」时才会渲染 <span class="mono">?token=</span>，未知密码时只给入口地址。
      </div>
      <div class="notice notice-warn" style="margin-top: 12px">
        SnowLuma 的 HTTP（3000）与 WS（3001）是两个独立 server，各有自己的 accessToken，务必分别填写。
        控制台不提供 SnowLuma 密码重置，请在其 WebUI 内修改。
      </div>
    </div>
  </div>
</template>
