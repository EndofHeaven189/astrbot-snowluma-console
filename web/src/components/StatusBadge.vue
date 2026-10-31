<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  state?: string | null
  label?: string
}>()

const text = computed(() => props.label ?? (props.state === 'running' ? '运行中' : props.state === 'stopped' ? '已停止' : props.state ?? '未知'))

const cls = computed(() => {
  const s = (props.state || '').toLowerCase()
  if (s === 'running' || s === 'ok' || s === 'done') return 'badge badge-running breathe'
  if (s === 'starting' || s === 'stopping' || s === 'pending') return 'badge badge-warn'
  if (s === 'failed' || s === 'fail' || s === 'error' || s === 'crashed') return 'badge badge-error'
  // FIX-AD：stopped 是正常状态，别用黄色警告色，和 idle 同色。
  if (s === 'stopped' || s === 'idle') return 'badge badge-idle'
  if (s === 'warn') return 'badge badge-warn'
  return 'badge badge-idle'
})
</script>

<template>
  <span :class="cls"><i class="dot" />{{ text }}</span>
</template>
