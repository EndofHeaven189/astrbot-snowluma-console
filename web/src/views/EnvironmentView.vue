<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import CheckList from '../components/CheckList.vue'
import { api } from '../api/client'
import { rawState, reportError, toast } from '../store'
import type { DetectResult } from '../api/types'

const state = rawState
const result = ref<DetectResult | null>(null)
const loading = ref(false)
const installing = ref<string | null>(null)

const route = computed(() => result.value?.route || state.settings?.astrbot?.deploy_mode || null)
const summary = computed(() => result.value?.summary)
const missing = computed(() =>
  (result.value?.checks || []).filter((c) => c.state === 'fail').map((c) => c.id),
)

const installable = computed(() => (result.value?.checks || []).filter((c) => c.state === 'fail' && c.id === 'uv'))

async function detect(force = false) {
  loading.value = true
  try {
    result.value = await api.detect(force)
    toast.ok('检测完成', `共 ${result.value.checks.length} 项，${result.value.summary.fail} 项不满足`)
  } catch (err) {
    reportError('环境检测失败', err)
  } finally {
    loading.value = false
  }
}

async function installDeps() {
  installing.value = 'uv'
  try {
    await api.installDeps(['uv'])
    toast.ok('已提交安装', 'uv 安装完成后请重新检测')
    await detect(true)
  } catch (err) {
    reportError('安装依赖失败', err)
  } finally {
    installing.value = null
  }
}

onMounted(() => detect(false))

const hintFor = (id: string): string | null => {
  const map: Record<string, string> = {
    python: 'https://www.python.org/downloads/windows/',
    node: 'https://nodejs.org/dist/v24.11.0/node-v24.11.0-x64.msi',
    docker: 'https://www.docker.com/products/docker-desktop/',
  }
  return map[id] || null
}
</script>

<template>
  <div class="stack-lg">
    <div class="card">
      <div class="card-head">
        <span>✓</span>
        <h2>环境检测</h2>
        <div class="card-actions">
          <button class="btn btn-sm btn-ghost" :disabled="loading" @click="detect(true)">
            {{ loading ? '检测中…' : '重新检测' }}
          </button>
        </div>
      </div>

      <div class="row wrap" style="gap: 10px">
        <span class="badge badge-idle">当前路线：{{ route === 'uv' ? 'uv / pip 原生' : route === 'docker' ? 'Docker' : '待选择' }}</span>
        <span v-if="summary" class="badge badge-ok">正常 {{ summary.ok }}</span>
        <span v-if="summary" class="badge badge-warn">注意 {{ summary.warn }}</span>
        <span v-if="summary" class="badge badge-error">不满足 {{ summary.fail }}</span>
        <span v-if="summary" class="badge badge-idle">待选择 {{ summary.pending }}</span>
      </div>

      <div v-if="result && !route" class="notice notice-warn" style="margin-top: 12px">
        还没有选择 AstrBot 部署路线，路线专属检测项显示为「待选择」。选择后会自动按该路线重新检测。
      </div>

      <div v-if="result && route && !result.can_install" class="notice notice-error" style="margin-top: 12px">
        存在阻塞项：{{ result.blocking.join('、') || '未知' }}。请先处理再安装。
      </div>

      <div v-if="installable.length" class="row wrap" style="margin-top: 12px">
        <button class="btn btn-primary btn-sm" :disabled="installing !== null" @click="installDeps">
          {{ installing ? '安装中…' : '一键安装 uv' }}
        </button>
        <span class="dim" style="font-size: 12px">通过当前 Python 的 pip 安装 uv，完成后重新检测。</span>
      </div>

      <div style="margin-top: 16px">
        <div v-if="!result" class="empty">{{ loading ? '检测中…' : '尚未检测' }}</div>
        <CheckList v-else :checks="result.checks" />
      </div>

      <div v-if="result" class="dim" style="font-size: 11.5px; margin-top: 10px">
        检测时间 {{ result.detect_at || '—' }} · 数据目录 <span class="mono">{{ result.root }}</span>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>💡</span>
        <h2>需要手动安装的组件</h2>
      </div>
      <p class="dim" style="font-size: 12.5px; margin-top: 0">
        控制台不会擅自装系统级软件。以下组件请手动安装后回到本页重新检测。
      </p>
      <div class="stack">
        <div v-if="missing.includes('node')" class="notice notice-warn">
          Node.js 未满足：SnowLuma lite 要求 <b>Node ≥ 24.22.1</b>。
          <a href="https://nodejs.org/dist/v24.11.0/node-v24.11.0-x64.msi" target="_blank" rel="noopener">下载 Node 24 LTS 安装包</a>
          —— 版本号不够会同样报错。
        </div>
        <div v-if="missing.includes('python')" class="notice notice-warn">
          Python 未满足：需要 3.10+（推荐 3.11/3.12）。
          <a href="https://www.python.org/downloads/windows/" target="_blank" rel="noopener">前往 python.org</a>
        </div>
        <div v-if="missing.includes('docker')" class="notice notice-warn">
          Docker 未检测到（仅 Docker 路线需要）。
          <a href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noopener">安装 Docker Desktop</a>
        </div>
        <div v-if="!missing.length" class="empty">没有需要手动处理的项目。</div>
      </div>
    </div>
  </div>
</template>
