<script setup lang="ts">
import { ref } from 'vue'
import LogPanel from '../components/LogPanel.vue'

const service = ref('astrbot')
const services = [
  { key: 'astrbot', label: 'AstrBot' },
  { key: 'snowluma', label: 'SnowLuma' },
  { key: 'installer', label: '安装器' },
  { key: 'console', label: '控制台' },
] as const
</script>

<template>
  <div class="stack-lg">
    <div class="card">
      <div class="card-head">
        <span>≡</span>
        <h2>实时日志</h2>
        <div class="card-actions dim" style="font-size: 12px">SSE 推送 · 每服务保留最近 5000 行</div>
      </div>

      <div class="row wrap" style="margin-bottom: 12px">
        <button
          v-for="s in services"
          :key="s.key"
          class="btn btn-sm"
          :class="service === s.key ? 'btn-primary' : 'btn-ghost'"
          @click="service = s.key"
        >
          {{ s.label }}
        </button>
      </div>

      <LogPanel :key="service" :service="service" height="calc(100vh - 320px)" />
    </div>
  </div>
</template>
