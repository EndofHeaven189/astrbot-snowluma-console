<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api } from '../api/client'
import { rawState, reloadEula, reloadSettings, reportError, toast } from '../store'

const state = rawState
const saving = ref(false)
const rootInput = ref('')
const changingRoot = ref(false)

const form = reactive({
  host: '127.0.0.1',
  port: 8765,
  dashboard_port: 6185,
  onebot_port: 6199,
  webui_port: 5099,
  http_port: 3000,
  ws_port: 3001,
  mirror: false,
  github_proxy: '',
  network_mode: 'bridge' as 'bridge' | 'host',
  shutdown_child: true,
  timeouts: {
    dependency_install: 600,
    service_ready: 180,
    stop_grace: 10,
    docker_cli: 60,
    compose_up: 300,
    password_capture: 180,
  } as Record<string, number>,
})

const TIMEOUT_LABELS: Array<{ key: string; label: string; hint: string }> = [
  { key: 'dependency_install', label: '依赖安装超时（秒）', hint: 'uv/pip 安装 AstrBot 的上限' },
  { key: 'service_ready', label: '首启就绪超时（秒）', hint: '启动后等端口+HTTP 就绪的上限' },
  { key: 'stop_grace', label: '优雅停止宽限（秒）', hint: '超时后强制结束进程树' },
  { key: 'docker_cli', label: 'Docker CLI 超时（秒）', hint: '单条 docker 命令' },
  { key: 'compose_up', label: 'compose up 超时（秒）', hint: '拉起容器' },
  { key: 'password_capture', label: '密码抓取窗口（秒）', hint: '首启后扫日志找一次性密码' },
]

const savingAdvanced = ref(false)

function syncForm() {
  const s = state.settings
  if (!s) return
  form.host = s.console.host
  form.port = s.console.port
  form.dashboard_port = s.astrbot.dashboard_port
  form.onebot_port = s.astrbot.onebot_port
  form.webui_port = s.snowluma.webui_port
  form.http_port = s.snowluma.http_port
  form.ws_port = s.snowluma.ws_port
  form.mirror = Boolean(s.mirror?.enabled)
  form.github_proxy = s.mirror?.github || ''
  form.network_mode = (s.astrbot.docker_network_mode as 'bridge' | 'host') || 'bridge'
  form.shutdown_child = s.lifecycle?.shutdown_child_processes !== false
  for (const item of TIMEOUT_LABELS) {
    const v = s.timeouts?.[item.key]
    if (typeof v === 'number') form.timeouts[item.key] = v
  }
  rootInput.value = state.meta?.root || ''
}

const rootPath = computed(() => state.meta?.dirs || {})

async function save() {
  saving.value = true
  try {
    await api.saveSettings({
      console: { host: form.host, port: form.port },
      astrbot: { dashboard_port: form.dashboard_port, onebot_port: form.onebot_port },
      snowluma: { webui_port: form.webui_port, http_port: form.http_port, ws_port: form.ws_port },
      bridge: { onebot_port: form.onebot_port },
      mirror: { enabled: form.mirror, github: form.github_proxy.trim() },
    })
    await reloadSettings()
    toast.ok('设置已保存', '端口变更后需要重启控制台/服务才会生效')
  } catch (err) {
    reportError('保存设置失败', err)
  } finally {
    saving.value = false
  }
}

async function saveAdvanced() {
  savingAdvanced.value = true
  try {
    const prev = state.settings?.astrbot?.docker_network_mode || 'bridge'
    const timeouts: Record<string, number> = {}
    for (const item of TIMEOUT_LABELS) {
      const v = Number(form.timeouts[item.key])
      if (!Number.isFinite(v) || v < 5 || v > 86400) {
        toast.error('超时值不合法', `${item.label} 需在 5~86400 秒之间`)
        return
      }
      timeouts[item.key] = Math.round(v)
    }
    await api.saveSettings({
      timeouts,
      lifecycle: { shutdown_child_processes: form.shutdown_child },
    })
    if (form.network_mode !== prev) {
      if (form.network_mode === 'host') {
        const res = await api.bridgeFallback()
        toast.ok('已切换为 network_mode: host', res.regenerated === false ? '（未生成 compose，AstrBot 可能还没装）' : 'compose 已重新生成，需重启 AstrBot')
      } else {
        const res = await api.networkMode('bridge')
        toast.ok('已切回 bridge 模式', res.regenerated === false ? '' : 'compose 已重新生成，需重启 AstrBot')
      }
    } else {
      toast.ok('高级设置已保存')
    }
    await reloadSettings()
    syncForm()
  } catch (err) {
    reportError('保存高级设置失败', err)
  } finally {
    savingAdvanced.value = false
  }
}

async function changeRoot() {
  if (!rootInput.value.trim()) return
  if (!window.confirm('切换数据目录到 ' + rootInput.value + '？\n\napps/downloads/backups/logs 会迁移，instances 不会被覆盖。')) return
  changingRoot.value = true
  try {
    const res = await api.changeRoot(rootInput.value.trim())
    state.meta = await api.meta()
    toast.ok('数据目录已切换', res.root || rootInput.value)
  } catch (err) {
    reportError('切换数据目录失败', err)
  } finally {
    changingRoot.value = false
  }
}

async function acceptEula() {
  try {
    await api.acceptEula({ eula: true, privacy: true })
    await reloadEula()
    toast.ok('已记录协议接受', '已写入 eula_acceptance.json')
  } catch (err) {
    reportError('记录失败', err)
  }
}

async function revokeEula() {
  if (!window.confirm('删除协议接受记录？删除后 SnowLuma 安装会重新要求勾选。')) return
  try {
    await api.revokeEula()
    await reloadEula()
    toast.info('已删除协议记录')
  } catch (err) {
    reportError('删除失败', err)
  }
}

onMounted(async () => {
  await reloadSettings()
  syncForm()
})
</script>

<template>
  <div class="stack-lg">
    <div class="card">
      <div class="card-head">
        <span>⚙</span>
        <h2>端口与监听</h2>
        <div class="card-actions">
          <button class="btn btn-sm btn-ghost" @click="syncForm">重置表单</button>
        </div>
      </div>

      <div class="grid cols-3">
        <div class="field">
          <label>控制台监听地址</label>
          <input v-model="form.host" class="mono" spellcheck="false" />
          <span class="dim" style="font-size: 11.5px">只允许 127.0.0.1 / localhost / ::1</span>
        </div>
        <div class="field">
          <label>控制台端口</label>
          <input v-model.number="form.port" type="number" class="mono" />
        </div>
        <div class="field">
          <label>AstrBot 面板端口</label>
          <input v-model.number="form.dashboard_port" type="number" class="mono" />
        </div>
        <div class="field">
          <label>AstrBot 反向 WS 端口</label>
          <input v-model.number="form.onebot_port" type="number" class="mono" />
        </div>
        <div class="field">
          <label>SnowLuma WebUI 端口</label>
          <input v-model.number="form.webui_port" type="number" class="mono" />
        </div>
        <div class="field">
          <label>SnowLuma HTTP 端口</label>
          <input v-model.number="form.http_port" type="number" class="mono" />
        </div>
        <div class="field">
          <label>SnowLuma WS 端口</label>
          <input v-model.number="form.ws_port" type="number" class="mono" />
        </div>
      </div>

      <label class="check" style="margin-top: 14px; font-size: 12.5px">
        <input v-model="form.mirror" type="checkbox" />启用国内镜像加速（pip / npm / GitHub 下载）
      </label>

      <div class="field" style="margin-top: 12px; max-width: 520px">
        <label>GitHub 下载代理前缀（可选）</label>
        <input
          v-model="form.github_proxy"
          class="mono"
          spellcheck="false"
          placeholder="https://ghfast.top"
        />
        <span class="dim" style="font-size: 11.5px">
          留空表示直连 GitHub。国内直连 Release 资产常只有几十 KB/s，填一个代理前缀（形如
          https://ghfast.top）可显著加速 SnowLuma 安装包下载；代理由你自行选择与信任。
        </span>
      </div>

      <div class="row" style="margin-top: 16px">
        <button class="btn btn-primary btn-sm" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : '保存设置' }}
        </button>
        <span class="dim" style="font-size: 12px">AstrBot 部署形态不在这里改，请到「安装部署」页切换。</span>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>🛠</span>
        <h2>高级</h2>
        <div class="card-actions">
          <StatusBadge :state="form.network_mode === 'host' ? 'warn' : 'ok'" :label="'Docker 网络：' + form.network_mode" />
        </div>
      </div>

      <div class="field" style="max-width: 560px">
        <label>AstrBot Docker 网络模式</label>
        <div class="row wrap" style="gap: 8px">
          <button class="btn btn-sm" :class="form.network_mode === 'bridge' ? 'btn-primary' : 'btn-ghost'" @click="form.network_mode = 'bridge'">
            bridge（默认，走 host.docker.internal）
          </button>
          <button class="btn btn-sm" :class="form.network_mode === 'host' ? 'btn-primary' : 'btn-ghost'" @click="form.network_mode = 'host'">
            host（退路，WSL2 端口映射不通时用）
          </button>
        </div>
        <span class="dim" style="font-size: 11.5px">
          仅影响 Docker 路线。改成 host 时容器直接用宿主机网络，不再做端口映射；保存会重新生成 compose.yml，需要重启 AstrBot 才生效。
        </span>
      </div>

      <label class="check" style="margin-top: 14px; font-size: 12.5px">
        <input v-model="form.shutdown_child" type="checkbox" />控制台退出时一并停止它拉起的子进程
      </label>
      <div class="dim" style="font-size: 11.5px; margin-left: 22px">
        关闭后控制台退出会留下服务进程，下次启动会在「服务」页提示接管或结束。
      </div>

      <div class="grid cols-3" style="margin-top: 16px">
        <div v-for="item in TIMEOUT_LABELS" :key="item.key" class="field">
          <label>{{ item.label }}</label>
          <input v-model.number="form.timeouts[item.key]" type="number" min="5" max="86400" class="mono" />
          <span class="dim" style="font-size: 11px">{{ item.hint }}</span>
        </div>
      </div>

      <div class="row" style="margin-top: 16px">
        <button class="btn btn-primary btn-sm" :disabled="savingAdvanced" @click="saveAdvanced">
          {{ savingAdvanced ? '保存中…' : '保存高级设置' }}
        </button>
        <button class="btn btn-sm btn-ghost" @click="syncForm">还原</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>📁</span>
        <h2>数据目录</h2>
      </div>
      <dl class="kv">
        <dt>根目录</dt>
        <dd class="mono">{{ state.meta?.root || '—' }}</dd>
        <dt>apps</dt>
        <dd class="mono">{{ rootPath.apps || '—' }}</dd>
        <dt>instances</dt>
        <dd class="mono">{{ rootPath.instances || '—' }}</dd>
        <dt>downloads</dt>
        <dd class="mono">{{ rootPath.downloads || '—' }}</dd>
        <dt>backups</dt>
        <dd class="mono">{{ rootPath.backups || '—' }}</dd>
        <dt>logs</dt>
        <dd class="mono">{{ rootPath.logs || '—' }}</dd>
        <dt>state</dt>
        <dd class="mono">{{ rootPath.state || '—' }}</dd>
      </dl>
      <div class="row wrap" style="margin-top: 14px">
        <input v-model="rootInput" class="mono" style="flex: 1 1 320px" spellcheck="false" />
        <button class="btn btn-sm" :disabled="changingRoot" @click="changeRoot">
          {{ changingRoot ? '切换中…' : '切换根目录' }}
        </button>
      </div>
      <div class="notice notice-warn" style="margin-top: 12px">
        迁移只拷贝 apps / downloads / backups / logs；<b>instances 目录永不覆盖</b>。要迁移到新机器时，只拷 instances 即可。
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>📜</span>
        <h2>协议接受记录</h2>
        <div class="card-actions">
          <StatusBadge :state="state.eula?.accepted ? 'ok' : 'warn'" :label="state.eula?.accepted ? '已接受' : '无记录'" />
        </div>
      </div>
      <dl class="kv">
        <dt>EULA</dt>
        <dd>{{ state.eula?.eula || '未接受' }}</dd>
        <dt>隐私政策</dt>
        <dd>{{ state.eula?.privacy || '未接受' }}</dd>
        <dt>接受时间</dt>
        <dd class="mono">{{ state.eula?.accepted_at || '—' }}</dd>
        <dt>版本</dt>
        <dd>{{ state.eula?.version || '—' }}</dd>
        <dt>方式</dt>
        <dd>{{ state.eula?.mode === 'manual' ? '人工确认' : state.eula?.mode === 'checkbox' ? '界面勾选' : '—' }}</dd>
        <dt>记录文件</dt>
        <dd class="mono">{{ state.eula?.path || '—' }}</dd>
      </dl>
      <div class="row wrap" style="margin-top: 14px">
        <button class="btn btn-sm" @click="acceptEula">记录协议接受</button>
        <button class="btn btn-danger btn-sm" :disabled="!state.eula?.accepted" @click="revokeEula">删除记录</button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>ℹ️</span>
        <h2>关于</h2>
      </div>
      <dl class="kv">
        <dt>名称</dt>
        <dd>{{ state.meta?.name || 'AstrBot × SnowLuma 集成控制台' }}</dd>
        <dt>版本</dt>
        <dd class="mono">{{ state.meta?.version || '0.1.1' }}</dd>
        <dt>平台</dt>
        <dd class="mono">{{ state.meta?.platform || '—' }}</dd>
        <dt>配置文件</dt>
        <dd class="mono">{{ state.meta?.settings_file || '—' }}</dd>
      </dl>
      <p class="dim" style="font-size: 12px; margin-bottom: 0; margin-top: 10px">
        控制台只监听本机回环地址，v0.1.1 不提供登录鉴权，请勿把端口暴露到公网。
      </p>
    </div>
  </div>
</template>
