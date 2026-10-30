<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import ToastHost from './components/ToastHost.vue'
import { serviceByName, useStore } from './store'

import OverviewView from './views/OverviewView.vue'
import EnvironmentView from './views/EnvironmentView.vue'
import InstallView from './views/InstallView.vue'
import ServicesView from './views/ServicesView.vue'
import ConfigView from './views/ConfigView.vue'
import CredentialsView from './views/CredentialsView.vue'
import BridgeView from './views/BridgeView.vue'
import LogsView from './views/LogsView.vue'
import SettingsView from './views/SettingsView.vue'

const { state, bootstrap, startPolling, stopPolling } = useStore()

type ViewKey =
  | 'overview'
  | 'env'
  | 'install'
  | 'services'
  | 'config'
  | 'credential'
  | 'bridge'
  | 'logs'
  | 'settings'

interface NavItem {
  key: ViewKey
  label: string
  icon: string
  sub: string
  component: any
}

const nav: NavItem[] = [
  { key: 'overview', label: '概览', icon: '◎', sub: '两个服务的运行状态与快捷入口', component: OverviewView },
  { key: 'env', label: '环境检测', icon: '✓', sub: '按部署路线动态检查依赖与端口', component: EnvironmentView },
  { key: 'install', label: '安装部署', icon: '↓', sub: 'AstrBot 双路线与 SnowLuma 安装', component: InstallView },
  { key: 'services', label: '服务管理', icon: '⏻', sub: '启停、重启与就绪状态', component: ServicesView },
  { key: 'config', label: '配置管理', icon: '⌘', sub: '读写配置并自动备份', component: ConfigView },
  { key: 'credential', label: '凭据库', icon: '🔑', sub: '面板密码与令牌，仅本机可见', component: CredentialsView },
  { key: 'bridge', label: '桥接向导', icon: '⇄', sub: 'AstrBot ⇄ SnowLuma 反向 WS', component: BridgeView },
  { key: 'logs', label: '实时日志', icon: '≡', sub: 'SSE 推送，保留最近 5000 行', component: LogsView },
  { key: 'settings', label: '设置', icon: '⚙', sub: '端口、镜像与数据目录', component: SettingsView },
]

const VIEW_KEYS = nav.map((n) => n.key)

function readHash(): ViewKey {
  const raw = (window.location.hash || '').replace(/^#\/?/, '').trim()
  return (VIEW_KEYS as string[]).includes(raw) ? (raw as ViewKey) : 'overview'
}

const current = ref<ViewKey>(readHash())
const collapsed = ref(window.innerWidth < 1280)

function go(key: ViewKey) {
  current.value = key
  if (readHash() !== key) window.location.hash = '#/' + key
}

function onHashChange() {
  const next = readHash()
  if (next !== current.value) current.value = next
}

const active = computed(() => nav.find((n) => n.key === current.value) || nav[0])
const services = serviceByName

const toggleSidebar = () => (collapsed.value = !collapsed.value)

function onResize() {
  if (window.innerWidth < 1280) collapsed.value = true
}
function onKey(ev: KeyboardEvent) {
  if (ev.key === '[' && (ev.ctrlKey || ev.metaKey)) {
    ev.preventDefault()
    toggleSidebar()
  }
}

watch(current, () => {
  document.querySelector('.content')?.scrollTo({ top: 0 })
})

onMounted(async () => {
  await bootstrap()
  startPolling(4000)
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKey)
  window.addEventListener('hashchange', onHashChange)
})
onBeforeUnmount(() => {
  stopPolling()
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('hashchange', onHashChange)
})

const runningCount = computed(() => state.services.filter((s) => s.running).length)
</script>

<template>
  <div class="shell">
    <aside class="sidebar" :class="{ collapsed }">
      <div class="sidebar-brand">
        <div class="brand-mark">AS</div>
        <div class="brand-text">
          <span class="brand-title">集成控制台</span>
          <span class="brand-version">v{{ state.meta?.version || '0.1.1' }}</span>
        </div>
      </div>

      <nav class="nav">
        <div
          v-for="item in nav"
          :key="item.key"
          class="nav-item"
          :class="{ active: current === item.key }"
          :title="item.label"
          @click="go(item.key)"
        >
          <span class="nav-icon">{{ item.icon }}</span>
          <span class="nav-label">{{ item.label }}</span>
          <span
            v-if="item.key === 'services' && runningCount"
            class="nav-badge badge badge-running"
            style="margin-left: auto; padding: 1px 6px"
            >{{ runningCount }}</span
          >
        </div>
      </nav>

      <div class="sidebar-foot">
        <button class="btn btn-ghost btn-sm" style="width: 100%" @click="toggleSidebar">
          {{ collapsed ? '»' : '« 收起' }}
        </button>
      </div>
    </aside>

    <section class="main">
      <header class="topbar">
        <div>
          <div class="topbar-title">{{ active.label }}</div>
          <div class="topbar-sub">{{ active.sub }}</div>
        </div>
        <div class="topbar-spacer" />
        <span v-if="state.backendDown" class="badge badge-error"><i class="dot" />后端未连接</span>
        <span v-else class="badge badge-ok"><i class="dot" />后端在线</span>
        <span class="badge" :class="services.astrbot?.running ? 'badge-running breathe' : 'badge-idle'">
          <i class="dot" />AstrBot
        </span>
        <span class="badge" :class="services.snowluma?.running ? 'badge-running breathe' : 'badge-idle'">
          <i class="dot" />SnowLuma
        </span>
      </header>

      <main class="content">
        <div class="content-inner">
          <component :is="active.component" :key="active.key" />
        </div>
      </main>
    </section>

    <ToastHost />
  </div>
</template>
