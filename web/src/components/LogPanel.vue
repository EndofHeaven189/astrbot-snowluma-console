<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'

const props = defineProps<{ service: string; height?: string }>()

const lines = ref<string[]>([])
const follow = ref(true)
const connected = ref(false)
const boxRef = ref<HTMLDivElement | null>(null)
let source: EventSource | null = null

const MAX = 5000

function append(line: string) {
  lines.value.push(line)
  if (lines.value.length > MAX) lines.value.splice(0, lines.value.length - MAX)
  void scroll()
}

async function scroll() {
  if (!follow.value) return
  await nextTick()
  const el = boxRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function loadHistory() {
  try {
    const res = await api.recentLog(props.service, 500)
    lines.value = res.lines.slice(-MAX)
    await scroll()
  } catch {
    /* 无历史也继续 */
  }
}

function openStream() {
  closeStream()
  const src = new EventSource(api.logUrl(props.service, 200))
  source = src
  src.onopen = () => (connected.value = true)
  src.onerror = () => (connected.value = false)
  src.onmessage = (event) => {
    try {
      const payload = JSON.parse(event.data)
      if (payload.type === 'log' && typeof payload.line === 'string') append(payload.line)
    } catch {
      /* 忽略非 JSON 心跳 */
    }
  }
}

function closeStream() {
  if (source) {
    source.close()
    source = null
    connected.value = false
  }
}

watch(
  () => props.service,
  async () => {
    lines.value = []
    await loadHistory()
    openStream()
  },
)

onMounted(async () => {
  await loadHistory()
  openStream()
})

onBeforeUnmount(closeStream)

const clear = () => {
  lines.value = []
}
</script>

<template>
  <div class="stack">
    <div class="row wrap">
      <span class="badge" :class="connected ? 'badge-ok' : 'badge-idle'">
        <i class="dot" />{{ connected ? '实时连接中' : '未连接' }}
      </span>
      <span class="dim" style="font-size: 12px">{{ lines.length }} 行</span>
      <div class="right row">
        <label class="check" style="font-size: 12px">
          <input v-model="follow" type="checkbox" />自动滚动
        </label>
        <button class="btn btn-sm btn-ghost" @click="clear">清空</button>
        <button class="btn btn-sm btn-ghost" @click="loadHistory">重新加载</button>
      </div>
    </div>
    <div ref="boxRef" class="logbox" :style="height ? { height } : undefined">
      <div v-if="!lines.length" class="dim">暂无日志。启动服务后会实时推送到这里。</div>
      <div v-for="(line, i) in lines" :key="i" class="logline">
        <span class="ln">{{ i + 1 }}</span><span>{{ line }}</span>
      </div>
    </div>
  </div>
</template>
