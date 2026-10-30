<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import ServiceCard from '../components/ServiceCard.vue'
import LogPanel from '../components/LogPanel.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { api } from '../api/client'
import { rawState, reportError, toast } from '../store'
import type { OrphanEntry } from '../api/types'

const state = rawState
const logService = ref<'astrbot' | 'snowluma' | 'installer' | 'console'>('astrbot')
const readyBusy = ref(false)

const options = [
  { key: 'astrbot', label: 'AstrBot' },
  { key: 'snowluma', label: 'SnowLuma' },
  { key: 'installer', label: '安装器' },
  { key: 'console', label: '控制台' },
] as const

const readyUrl = computed(() => {
  const s = state.services.find((x) => x.name === logService.value)
  return s?.url || null
})

// ---- FIX-07：上次未正常退出留下的服务进程
const orphans = ref<Record<string, OrphanEntry>>({})
const orphanCount = computed(() => Object.keys(orphans.value).length)
const orphanList = computed(() => Object.values(orphans.value))
const scanning = ref(false)
const resolving = ref<string | null>(null)

async function loadOrphans() {
  try {
    const res = await api.orphans()
    orphans.value = res.orphans || {}
  } catch {
    /* 忽略：老版本后端可能没有这个接口 */
  }
}

async function scanOrphans() {
  scanning.value = true
  try {
    const res = await api.scanOrphans()
    orphans.value = res.orphans || {}
    toast.info('已重新扫描', `发现 ${res.count} 个残留服务进程`)
  } catch (err) {
    reportError('扫描失败', err)
  } finally {
    scanning.value = false
  }
}

async function resolveOrphan(service: string, action: 'kill' | 'adopt') {
  resolving.value = service + action
  try {
    await api.resolveOrphan(service, action)
    toast.ok(action === 'kill' ? '已结束残留进程' : '已接管', service)
    await loadOrphans()
  } catch (err) {
    reportError('处理失败', err)
  } finally {
    resolving.value = null
  }
}

async function checkReady() {
  if (logService.value !== 'astrbot' && logService.value !== 'snowluma') return
  readyBusy.value = true
  try {
    const r = await fetch(`/api/service/${logService.value}/ready?timeout=15`).then((x) => x.json())
    if (r.ready) toast.ok('服务已就绪', r.url || '')
    else toast.error('服务尚未就绪', r.error || '超时')
  } catch (err) {
    reportError('就绪检查失败', err)
  } finally {
    readyBusy.value = false
  }
}

onMounted(loadOrphans)
</script>

<template>
  <div class="stack-lg">
    <div v-if="orphanCount" class="card" style="border-color: rgba(245, 158, 11, 0.5)">
      <div class="card-head">
        <span>⚠️</span>
        <h2>检测到上次未正常退出的服务进程</h2>
        <div class="card-actions">
          <StatusBadge state="warn" :label="orphanCount + ' 个'" />
          <button class="btn btn-sm btn-ghost" :disabled="scanning" @click="scanOrphans">
            {{ scanning ? '扫描中…' : '重新扫描' }}
          </button>
        </div>
      </div>
      <p class="dim" style="font-size: 12.5px; margin-top: 0">
        控制台启动时在 <span class="mono">state\pids.json</span> 里发现了仍然存活、且命令行匹配已知服务的进程。
        直接「启动」会产生第二个实例，请先选择「接管」或「结束」。
      </p>
      <div v-for="item in orphanList" :key="item.service" class="row wrap" style="gap: 10px; padding: 8px 0; border-top: 1px solid var(--border)">
        <b style="min-width: 90px">{{ item.service }}</b>
        <span class="mono dim" style="font-size: 12px">PID {{ item.pid }}</span>
        <span class="mono dim" style="font-size: 12px">端口 {{ item.port ?? '—' }}</span>
        <span class="mono dim" style="font-size: 12px; flex: 1 1 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
          {{ item.cmd || '' }}
        </span>
        <button class="btn btn-sm" :disabled="resolving === item.service + 'adopt'" @click="resolveOrphan(item.service, 'adopt')">
          接管
        </button>
        <button class="btn btn-danger btn-sm" :disabled="resolving === item.service + 'kill'" @click="resolveOrphan(item.service, 'kill')">
          结束进程
        </button>
      </div>
    </div>

    <div class="grid cols-2">
      <ServiceCard name="astrbot" title="AstrBot" subtitle="机器人框架" icon="🤖" />
      <ServiceCard name="snowluma" title="SnowLuma" subtitle="OneBot v11 协议端" icon="❄️" />
    </div>

    <div class="card">
      <div class="card-head">
        <span>≡</span>
        <h2>服务日志</h2>
        <div class="card-actions row">
          <button class="btn btn-sm btn-ghost" :disabled="readyBusy" @click="checkReady">就绪检查</button>
          <a v-if="readyUrl" class="btn btn-sm btn-ghost" :href="readyUrl" target="_blank" rel="noopener">打开面板</a>
        </div>
      </div>

      <div class="row wrap" style="margin-bottom: 12px">
        <button
          v-for="opt in options"
          :key="opt.key"
          class="btn btn-sm"
          :class="logService === opt.key ? 'btn-primary' : 'btn-ghost'"
          @click="logService = opt.key"
        >
          {{ opt.label }}
        </button>
      </div>

      <LogPanel :service="logService" height="420px" />
    </div>
  </div>
</template>
