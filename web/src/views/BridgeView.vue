<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api } from '../api/client'
import { rawState, reportError, toast } from '../store'
import type { BridgeEndpoint, BridgeStatus } from '../api/types'

const state = rawState
const status = ref<BridgeStatus | null>(null)
const endpoint = ref<BridgeEndpoint | null>(null)
const token = ref('')
const port = ref<number>(6199)
const busy = ref(false)
const verifying = ref(false)
const refreshing = ref(false)
const fallingBack = ref(false)
const verifyResult = ref<Record<string, any> | null>(null)

const currentToken = computed(() => (status.value?.token_set ? '已设置（留空则保持不变）' : '未设置（留空将自动生成）'))
const mode = computed(() => state.settings?.astrbot?.deploy_mode ?? status.value?.deploy_mode ?? null)
const isDocker = computed(() => mode.value === 'docker')
const networkMode = computed(() => endpoint.value?.network_mode || state.settings?.astrbot?.docker_network_mode || 'bridge')

const checks = computed<Array<Record<string, any>>>(() => (verifyResult.value?.checks as any[]) || [])

const strategyLabel: Record<string, string> = {
  'host-gateway': 'Docker host-gateway（host.docker.internal）',
  'host-network': 'network_mode: host（容器直接用宿主机网络）',
  'lan-ip': '宿主机局域网 IP 退路',
  loopback: '本机回环',
}

async function load() {
  try {
    status.value = await api.bridgeStatus()
    port.value = status.value.onebot_port
    endpoint.value = (status.value.endpoint as BridgeEndpoint) || null
    if (!endpoint.value) {
      try {
        const res = await api.bridgeEndpoint(false)
        endpoint.value = res.endpoint
      } catch {
        /* 忽略 */
      }
    }
  } catch (err) {
    reportError('读取桥接状态失败', err)
  }
}

async function refreshEndpoint() {
  refreshing.value = true
  try {
    const res = await api.bridgeEndpoint(true)
    endpoint.value = res.endpoint
    if (res.endpoint?.reachable === false) {
      toast.error('容器内无法连到宿主机', res.endpoint?.note || '')
    } else {
      toast.ok('已探测桥接地址', res.endpoint?.url || '')
    }
  } catch (err) {
    reportError('探测桥接地址失败', err)
  } finally {
    refreshing.value = false
  }
}

async function switchFallback() {
  if (
    !window.confirm(
      '把 AstrBot 的 Docker 网络模式切换为 host，并重新生成 compose？\n\n' +
        '容器将直接使用宿主机网络，端口不再做映射；切换后需要重新执行一次「启动」。',
    )
  )
    return
  fallingBack.value = true
  try {
    const res = await api.bridgeFallback()
    endpoint.value = res.endpoint
    toast.ok('已切换到 network_mode: host', '请重启 AstrBot 让新 compose 生效')
    await load()
  } catch (err) {
    reportError('切换退路失败', err)
  } finally {
    fallingBack.value = false
  }
}

async function apply() {
  busy.value = true
  try {
    const res = await api.bridgeApply({
      token: token.value ? token.value : undefined,
      onebot_port: port.value,
    })
    status.value = res.status
    if (res.endpoint) endpoint.value = res.endpoint
    verifyResult.value = null
    toast.ok('桥接配置已写入', res.token_generated ? '已自动生成 Token 并写入两侧' : '两侧配置已对齐')
    token.value = ''
    await load()
  } catch (err) {
    reportError('写入桥接配置失败', err)
  } finally {
    busy.value = false
  }
}

async function verify() {
  verifying.value = true
  try {
    const res = await api.bridgeVerify()
    verifyResult.value = res
    status.value = res.status
    if (res.endpoint) endpoint.value = res.endpoint
    if (res.ok) toast.ok('连通性正常', '所有检查项通过')
    else toast.error('连通性检查未通过', (res.hints || []).join('；'))
  } catch (err) {
    reportError('连通性检查失败', err)
  } finally {
    verifying.value = false
  }
}

async function genToken() {
  token.value = Array.from(crypto.getRandomValues(new Uint8Array(24)))
    .map((b) => b.toString(36))
    .join('')
    .slice(0, 32)
  toast.info('已生成新 Token', '点「一键对齐」才会写入两侧配置')
}

onMounted(load)
</script>

<template>
  <div class="stack-lg">
    <div class="card">
      <div class="card-head">
        <span>⇄</span>
        <h2>桥接向导</h2>
        <div class="card-actions">
          <StatusBadge :state="status?.consistent ? 'ok' : 'warn'" :label="status?.consistent ? '两侧已对齐' : '待对齐'" />
          <button class="btn btn-sm btn-ghost" @click="load">刷新</button>
        </div>
      </div>

      <p class="dim" style="font-size: 12.5px; margin-top: 0">
        AstrBot 开启 <b>反向 WebSocket 服务端</b>（默认 6199），SnowLuma 作为客户端连过去，两侧 Token 必须一致。
        控制台会同时改写两侧配置文件并分别备份。目标地址由部署形态决定，不再写死。
      </p>

      <div v-if="endpoint" class="card" style="padding: 14px; margin-bottom: 14px">
        <div class="row wrap" style="margin-bottom: 10px">
          <b>SnowLuma 应连接的目标</b>
          <StatusBadge
            class="right"
            :state="endpoint.reachable === false ? 'error' : 'ok'"
            :label="endpoint.reachable === false ? '不可达' : endpoint.reachable === true ? '已探测可达' : '未探测'"
          />
        </div>
        <div class="mono" style="font-size: 15px; margin-bottom: 10px">{{ endpoint.url || '—' }}</div>
        <dl class="kv">
          <dt>部署形态</dt>
          <dd>{{ endpoint.route || mode || '未选择' }}</dd>
          <dt>选中的策略</dt>
          <dd>{{ strategyLabel[String(endpoint.strategy)] || endpoint.strategy || '—' }}</dd>
          <dt>Docker 网络模式</dt>
          <dd class="mono">{{ endpoint.network_mode || (isDocker ? networkMode : '—') }}</dd>
          <dt>拨号方</dt>
          <dd class="mono">{{ endpoint.dial_from || '—' }}</dd>
        </dl>
        <div v-if="(endpoint.candidates || []).length" style="margin-top: 10px">
          <div class="dim" style="font-size: 12px; margin-bottom: 6px">候选地址（按优先级，容器内自动探测取第一个可达）</div>
          <div
            v-for="(c, i) in endpoint.candidates"
            :key="i"
            class="row wrap mono"
            style="gap: 10px; font-size: 12px; padding: 4px 0; border-top: 1px solid var(--border)"
          >
            <span style="min-width: 130px">{{ strategyLabel[c.strategy] || c.strategy }}</span>
            <span style="flex: 1 1 240px">{{ c.url }}</span>
            <StatusBadge
              v-if="c.reachable !== undefined && c.reachable !== null"
              :state="c.reachable ? 'ok' : 'warn'"
              :label="c.reachable ? '可达' : '不可达'"
            />
          </div>
        </div>
        <div v-if="endpoint.note" class="notice notice-warn" style="margin-top: 10px">{{ endpoint.note }}</div>
        <div class="row wrap" style="margin-top: 12px">
          <button v-if="isDocker" class="btn btn-sm" :disabled="refreshing" @click="refreshEndpoint">
            {{ refreshing ? '探测中…' : '容器内自动探测' }}
          </button>
          <button
            v-if="isDocker && networkMode !== 'host'"
            class="btn btn-sm btn-ghost"
            :disabled="fallingBack"
            @click="switchFallback"
          >
            {{ fallingBack ? '切换中…' : '一键切换退路（network_mode: host）' }}
          </button>
        </div>
      </div>

      <div class="grid cols-2" style="margin-bottom: 14px">
        <div class="card" style="padding: 14px">
          <div class="row" style="margin-bottom: 8px">
            <b>AstrBot 侧</b>
            <StatusBadge class="right" :state="status?.astrbot.configured ? 'ok' : 'warn'" :label="status?.astrbot.configured ? '已配置' : '未配置'" />
          </div>
          <dl class="kv">
            <dt>配置文件</dt><dd class="mono" style="font-size: 11.5px">{{ status?.astrbot.path || '—' }}</dd>
            <dt>适配器</dt><dd>{{ status?.astrbot.configured ? 'aiocqhttp（OneBot v11）' : '尚未生成' }}</dd>
            <dt>监听地址</dt><dd class="mono">{{ status?.astrbot.host || '—' }}:{{ status?.astrbot.port || '—' }}</dd>
            <dt>启用</dt><dd>{{ status?.astrbot.enabled ? '是' : '否' }}</dd>
            <dt>Token</dt><dd>{{ status?.astrbot.token_set ? '已设置' : '未设置' }}</dd>
          </dl>
        </div>
        <div class="card" style="padding: 14px">
          <div class="row" style="margin-bottom: 8px">
            <b>SnowLuma 侧</b>
            <StatusBadge class="right" :state="status?.snowluma.configured ? 'ok' : 'warn'" :label="status?.snowluma.configured ? '已配置' : '未配置'" />
          </div>
          <dl class="kv">
            <dt>配置文件</dt><dd class="mono" style="font-size: 11.5px">{{ status?.snowluma.path || '—' }}</dd>
            <dt>WS 客户端</dt><dd class="mono">{{ status?.snowluma.url || '—' }}</dd>
            <dt>启用</dt><dd>{{ status?.snowluma.enabled ? '是' : '否' }}</dd>
            <dt>Token</dt><dd>{{ status?.snowluma.token_set ? '已设置' : '未设置' }}</dd>
            <dt>HTTP/WS server</dt><dd>{{ status?.snowluma.http_servers ?? 0 }} / {{ status?.snowluma.ws_servers ?? 0 }}</dd>
          </dl>
        </div>
      </div>

      <div class="grid cols-2" style="gap: 14px; align-items: end">
        <div class="field">
          <label>OneBot 反向 WS 端口</label>
          <input v-model.number="port" type="number" min="1" max="65535" class="mono" />
        </div>
        <div class="field">
          <label>共享 Token</label>
          <div class="row" style="gap: 8px">
            <input v-model="token" type="text" class="mono" :placeholder="currentToken" spellcheck="false" />
            <button class="btn btn-sm btn-ghost" @click="genToken">生成</button>
          </div>
        </div>
      </div>

      <div class="row wrap" style="margin-top: 16px">
        <button class="btn btn-primary btn-sm" :disabled="busy" @click="apply">
          {{ busy ? '写入中…' : '一键对齐两侧配置' }}
        </button>
        <button class="btn btn-sm" :disabled="verifying" @click="verify">
          {{ verifying ? '检查中…' : '检查连通性' }}
        </button>
        <span class="dim right" style="font-size: 12px">写入后需重启 AstrBot 与 SnowLuma 才生效</span>
      </div>

      <div v-if="status?.hint" class="notice notice-warn" style="margin-top: 12px">{{ status.hint }}</div>
    </div>

    <div v-if="verifyResult" class="card">
      <div class="card-head">
        <span>🔍</span>
        <h2>连通性检查结果</h2>
        <div class="card-actions">
          <StatusBadge :state="verifyResult.ok ? 'ok' : 'error'" :label="verifyResult.ok ? '通过' : '未通过'" />
        </div>
      </div>

      <div v-if="checks.length" style="margin-bottom: 12px">
        <div
          v-for="(c, i) in checks"
          :key="i"
          class="row wrap"
          style="gap: 10px; padding: 6px 0; border-top: 1px solid var(--border); font-size: 12.5px"
        >
          <StatusBadge :state="c.ok ? 'ok' : 'error'" :label="c.ok ? '通过' : '未通过'" />
          <b style="min-width: 200px">{{ c.label || c.name }}</b>
          <span class="dim mono" style="flex: 1 1 240px">{{ c.detail || '' }}</span>
        </div>
      </div>

      <dl class="kv">
        <dt>配置一致</dt>
        <dd>{{ verifyResult.status?.consistent ? '是' : '否' }}</dd>
        <dt>部署形态</dt>
        <dd>{{ verifyResult.status?.deploy_mode || '未选择' }}</dd>
        <dt>检查时间</dt>
        <dd class="mono">{{ verifyResult.checked_at }}</dd>
      </dl>

      <div v-for="(h, i) in verifyResult.hints || []" :key="i" class="notice notice-warn" style="margin-top: 10px">{{ h }}</div>

      <div v-if="isDocker" class="card" style="padding: 12px; margin-top: 12px">
        <b>Docker 路线专项排查</b>
        <ol class="dim" style="font-size: 12.5px; line-height: 1.85; padding-left: 18px; margin: 8px 0 0">
          <li>执行 <span class="mono">docker ps</span>，确认 {{ port }} 端口映射显示为 <span class="mono">0.0.0.0:{{ port }}-&gt;{{ port }}/tcp</span>（WSL2 下只绑回环地址时 Windows 侧常连不上）。</li>
          <li>点上面的「容器内自动探测」：控制台会在容器里尝试 host.docker.internal 与局域网 IP，取第一个可达的。</li>
          <li>都不可达时点「一键切换退路」，把 AstrBot 改为 <span class="mono">network_mode: host</span> 并重新生成 compose，然后重启 AstrBot。</li>
        </ol>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>📖</span>
        <h2>手工核对清单</h2>
      </div>
      <ol class="dim" style="font-size: 12.5px; line-height: 1.9; padding-left: 18px; margin: 0">
        <li>AstrBot 面板 → 配置 → 平台适配器里能看到 <span class="mono">aiocqhttp</span>，端口 {{ port }}，Token 非空。</li>
        <li>SnowLuma <span class="mono">config\onebot.json</span> → <span class="mono">networks.wsClients</span> 里有一条指向上面「应连接的目标」的客户端。</li>
        <li>两个服务都重启后，AstrBot 日志出现「WebSocket 连接建立」之类字样。</li>
        <li>在 QQ 里给机器人发消息，能收到回复。</li>
      </ol>
    </div>
  </div>
</template>
