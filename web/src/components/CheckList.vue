<script setup lang="ts">
import type { CheckItem } from '../api/types'

defineProps<{ checks: CheckItem[] }>()

const cls = (s: string) => {
  if (s === 'ok') return 'badge badge-ok'
  if (s === 'warn') return 'badge badge-warn'
  if (s === 'fail') return 'badge badge-error'
  return 'badge badge-idle'
}
/** scope 是后端给的所属路线；不认识的取值不要硬凑成 Docker，直接不显示。 */
const SCOPE_LABELS: Record<string, string> = {
  uv: '仅 uv/pip 路线',
  docker: '仅 Docker 路线',
  snowluma: 'SnowLuma 需要',
}
const scopeLabel = (s?: string): string => (s ? SCOPE_LABELS[s] || '' : '')

const label = (s: string) =>
  ({ ok: '正常', warn: '注意', fail: '不满足', pending: '待选择' } as Record<string, string>)[s] || s

/** 把后端 value（可能是数字、字符串、对象）渲染成一行短文本。 */
const fmt = (v: unknown): string => {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(1)
  if (typeof v === 'string') return v
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>
    if (typeof o.version === 'string' && o.version) return o.version
    if (typeof o.path === 'string' && o.path) return o.path
    try {
      return JSON.stringify(v)
    } catch {
      return '—'
    }
  }
  return String(v)
}
</script>

<template>
  <table class="table">
    <thead>
      <tr>
        <th style="width: 38%">检测项</th>
        <th style="width: 12%">状态</th>
        <th>结果</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="c in checks" :key="c.id">
        <td>
          <div>{{ c.label }}</div>
          <div v-if="scopeLabel(c.scope)" class="dim" style="font-size: 11px">
            {{ scopeLabel(c.scope) }}
          </div>
        </td>
        <td><span :class="cls(c.state)">{{ label(c.state) }}</span></td>
        <td>
          <div>{{ fmt(c.value) }}</div>
          <div v-if="c.detail" class="dim" style="font-size: 11.5px">{{ c.detail }}</div>
          <div v-if="c.hint" class="dim" style="font-size: 11.5px">建议：{{ c.hint }}</div>
        </td>
      </tr>
    </tbody>
  </table>
</template>
