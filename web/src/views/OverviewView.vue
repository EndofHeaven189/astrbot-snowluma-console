<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ServiceCard from '../components/ServiceCard.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api } from '../api/client'
import { rawState, reportError } from '../store'
import type { BridgeStatus } from '../api/types'

const state = rawState
const bridge = ref<BridgeStatus | null>(null)

const modeText = computed(() => {
  const m = state.settings?.astrbot?.deploy_mode
  if (!m) return '尚未选择部署路线'
  return m === 'docker' ? 'Docker 容器' : 'uv / pip 原生'
})

const running = computed(() => state.services.filter((s) => s.running).length)

// FIX-BJ：面板/WebUI 端口读设置，subtitle 里不再写死 6185 / 5099。
const dashboardPort = computed(() => state.settings?.astrbot?.dashboard_port ?? '—')
const webuiPort = computed(() => state.settings?.snowluma?.webui_port ?? '—')

// FIX-BK：以 SnowLuma 实际配置为主，控制器建议地址只作对照。
const actualEndpoint = computed(() => bridge.value?.snowluma?.url || '')
const suggestedEndpoint = computed(() => bridge.value?.endpoint?.url || '')
const endpointMismatch = computed(
  () => Boolean(actualEndpoint.value && suggestedEndpoint.value && actualEndpoint.value !== suggestedEndpoint.value),
)

async function loadBridge() {
  try {
    bridge.value = await api.bridgeStatus()
  } catch (err) {
    reportError('读取桥接状态失败', err)
  }
}

onMounted(loadBridge)
</script>

<template>
  <div class="stack-lg">
    <div class="grid cols-3">
      <div class="card stat">
        <div class="stat-label">AstrBot 部署形态</div>
        <div class="stat-value">{{ modeText }}</div>
        <div class="dim" style="font-size: 12px">在「安装部署」页切换路线</div>
      </div>
      <div class="card stat">
        <div class="stat-label">运行中的服务</div>
        <div class="stat-value">{{ running }} / 2</div>
        <div class="dim" style="font-size: 12px">每 4 秒自动刷新</div>
      </div>
      <div class="card stat">
        <div class="stat-label">桥接一致性</div>
        <div class="stat-value">
          <StatusBadge :state="bridge?.consistent ? 'ok' : 'warn'" :label="bridge?.consistent ? '已对齐' : '待配置'" />
        </div>
        <div class="dim" style="font-size: 12px">OneBot 端口 {{ bridge?.onebot_port ?? state.settings?.bridge?.onebot_port ?? '—' }}</div>
      </div>
    </div>

    <div class="grid cols-2">
      <ServiceCard name="astrbot" title="AstrBot" :subtitle="`机器人框架 · 面板 ${dashboardPort}`" icon="🤖" />
      <ServiceCard name="snowluma" title="SnowLuma" :subtitle="`OneBot v11 协议端 · WebUI ${webuiPort}`" icon="❄️" />
    </div>

    <div class="card">
      <div class="card-head">
        <span>⇄</span>
        <h2>桥接状态</h2>
        <div class="card-actions">
          <button class="btn btn-sm btn-ghost" @click="loadBridge">刷新</button>
        </div>
      </div>
      <div v-if="bridge" class="grid cols-2">
        <div>
          <div class="dim" style="font-size: 12px">AstrBot 侧（反向 WS 服务端）</div>
          <dl class="kv">
            <dt>配置文件</dt>
            <dd class="mono">{{ bridge.astrbot.path || '未生成' }}</dd>
            <dt>端口</dt>
            <dd class="mono">{{ bridge.astrbot.port ?? '—' }}</dd>
            <dt>Token</dt>
            <dd>{{ bridge.astrbot.token_set ? '已设置' : '未设置' }}</dd>
          </dl>
        </div>
        <div>
          <div class="dim" style="font-size: 12px">SnowLuma 侧（WS 客户端）</div>
          <dl class="kv">
            <dt>配置文件</dt>
            <dd class="mono">{{ bridge.snowluma.path || '未生成' }}</dd>
            <dt>目标地址（实际配置）</dt>
            <dd class="mono">{{ actualEndpoint || '—' }}</dd>
            <dt>建议地址</dt>
            <dd class="mono">{{ suggestedEndpoint || '—' }}</dd>
            <dt>Token</dt>
            <dd>{{ bridge.snowluma.token_set ? '已设置' : '未设置' }}</dd>
          </dl>
        </div>
      </div>
      <div v-else class="empty">正在读取桥接状态…</div>
      <div v-if="endpointMismatch" class="notice notice-warn" style="margin-top: 12px">
        SnowLuma 实际连的是 <span class="mono">{{ actualEndpoint }}</span>，与控制台建议的
        <span class="mono">{{ suggestedEndpoint }}</span> 不一致。请到「桥接」页点「一键对齐」后重启两个服务。
      </div>
      <div v-if="bridge?.hint" class="notice notice-warn" style="margin-top: 12px">{{ bridge.hint }}</div>
    </div>

    <div v-if="!state.settings?.astrbot?.deploy_mode" class="notice notice-warn">
      还没有选择 AstrBot 部署路线。先到「环境检测」看看本机适合哪条路线，再到「安装部署」开始。
    </div>
  </div>
</template>
