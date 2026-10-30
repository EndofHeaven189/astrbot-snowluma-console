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
  if (s === 'stopped' || s === 'idle' || s === 'warn') return 'badge badge-warn'
  return 'badge badge-idle'
})
</script>

<template>
  <span :class="cls"><i class="dot" />{{ text }}</span>
</template>
