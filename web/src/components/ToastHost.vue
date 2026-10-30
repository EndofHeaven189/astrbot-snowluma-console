<script setup lang="ts">
import { rawState } from '../store'

const state = rawState
</script>

<template>
  <div class="toasts">
    <div v-for="t in state.toasts" :key="t.id" class="toast" :class="`toast-${t.kind}`">
      <div class="toast-title">{{ t.title }}</div>
      <div v-if="t.text" class="dim">{{ t.text }}</div>
    </div>
  </div>
</template>
