<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client'
import { reportError, toast } from '../store'
import type { BackupEntry, ConfigFileMeta } from '../api/types'

const service = ref<'astrbot' | 'snowluma'>('astrbot')
const files = ref<ConfigFileMeta[]>([])
const backups = ref<BackupEntry[]>([])
const activeId = ref<string | null>(null)
const text = ref('')
const original = ref('')
const path = ref('')
const loading = ref(false)
const saving = ref(false)
const lastBackup = ref<string | null>(null)

const dirty = computed(() => text.value !== original.value)
const canSave = computed(() => dirty.value && !saving.value && activeId.value !== null)

const jsonError = computed(() => {
  if (!text.value.trim()) return '内容为空'
  try {
    JSON.parse(text.value)
    return null
  } catch (err) {
    return err instanceof Error ? err.message : String(err)
  }
})

async function loadFiles() {
  loading.value = true
  try {
    const res = await api.configFiles(service.value)
    files.value = res.files
    if (!files.value.find((f) => f.id === activeId.value)) {
      activeId.value = files.value.find((f) => f.exists)?.id || files.value[0]?.id || null
    }
  } catch (err) {
    reportError('读取配置文件列表失败', err)
  } finally {
    loading.value = false
  }
}

async function loadFile() {
  if (!activeId.value) return
  loading.value = true
  try {
    const res = await api.configRead(service.value, activeId.value)
    const data = res.data ?? res.content ?? {}
    text.value = JSON.stringify(data, null, 2)
    original.value = text.value
    path.value = res.path || ''
  } catch (err) {
    reportError('读取配置失败', err)
  } finally {
    loading.value = false
  }
}

async function loadBackups() {
  try {
    const res = await api.backups(service.value)
    backups.value = res.backups
  } catch (err) {
    /* 忽略 */
  }
}

async function save() {
  if (!activeId.value) return
  if (jsonError.value) {
    toast.error('JSON 语法错误', jsonError.value)
    return
  }
  saving.value = true
  try {
    const data = JSON.parse(text.value)
    const res = await api.configWrite(service.value, activeId.value, data)
    original.value = text.value
    lastBackup.value = res.backup || null
    toast.ok('配置已保存', res.backup ? '已生成备份：' + res.backup : '')
    await loadBackups()
  } catch (err) {
    reportError('保存失败', err)
  } finally {
    saving.value = false
  }
}

function format() {
  if (jsonError.value) {
    toast.error('无法格式化', jsonError.value)
    return
  }
  text.value = JSON.stringify(JSON.parse(text.value), null, 2)
  toast.info('已格式化')
}

function revert() {
  text.value = original.value
}

async function restore(name: string) {
  if (!window.confirm('用备份 ' + name + ' 覆盖当前配置？当前文件会再备份一份。')) return
  try {
    const res = await api.restoreBackup(name)
    // FIX-S：后端返回字段是 restored，不是 path。
    toast.ok('已从备份还原', res.restored || name)
    await loadFile()
    await loadBackups()
  } catch (err) {
    reportError('还原失败', err)
  }
}

watch(service, async () => {
  activeId.value = null
  text.value = ''
  original.value = ''
  await loadFiles()
  await loadFile()
  await loadBackups()
})

watch(activeId, loadFile)

onMounted(async () => {
  await loadFiles()
  await loadFile()
  await loadBackups()
})
</script>

<template>
  <div class="stack-lg">
    <div class="card">
      <div class="card-head">
        <span>⌘</span>
        <h2>配置管理</h2>
        <div class="card-actions row">
          <button class="btn btn-sm btn-ghost" @click="loadFile">重新读取</button>
        </div>
      </div>

      <div class="row wrap" style="margin-bottom: 12px">
        <button class="btn btn-sm" :class="service === 'astrbot' ? 'btn-primary' : 'btn-ghost'" @click="service = 'astrbot'">
          AstrBot
        </button>
        <button class="btn btn-sm" :class="service === 'snowluma' ? 'btn-primary' : 'btn-ghost'" @click="service = 'snowluma'">
          SnowLuma
        </button>
      </div>

      <div class="grid cols-2" style="align-items: start; gap: 14px">
        <div>
          <div class="dim" style="font-size: 12px; margin-bottom: 8px">配置文件</div>
          <div class="stack" style="gap: 8px">
            <div
              v-for="f in files"
              :key="f.id"
              class="card choice"
              :class="{ selected: activeId === f.id, dim: !f.exists }"
              style="padding: 10px 12px; cursor: pointer"
              @click="activeId = f.id"
            >
              <div class="row" style="gap: 8px">
                <b style="font-size: 13px">{{ f.label }}</b>
                <span class="badge" :class="f.exists ? 'badge-ok' : 'badge-idle'" style="margin-left: auto">
                  {{ f.exists ? f.size_mb + ' MB' : '未生成' }}
                </span>
              </div>
              <div class="mono" style="font-size: 11px; margin-top: 4px">{{ f.path }}</div>
            </div>
            <div v-if="!files.length && !loading" class="empty">没有可管理的配置文件。</div>
          </div>
        </div>

        <div>
          <div class="row" style="margin-bottom: 8px">
            <span class="dim" style="font-size: 12px">编辑器</span>
            <span v-if="dirty" class="badge badge-warn" style="margin-left: 8px">未保存</span>
            <div class="right row">
              <button class="btn btn-sm btn-ghost" :disabled="jsonError !== null" @click="format">格式化</button>
              <button class="btn btn-sm btn-ghost" :disabled="!dirty" @click="revert">撤销</button>
              <button class="btn btn-primary btn-sm" :disabled="!canSave || jsonError !== null" @click="save">
                {{ saving ? '保存中…' : '保存（自动备份）' }}
              </button>
            </div>
          </div>
          <textarea
            v-model="text"
            class="mono"
            spellcheck="false"
            style="width: 100%; min-height: 420px; resize: vertical; font-size: 12px; line-height: 1.55"
          />
          <div v-if="jsonError" class="notice notice-error" style="margin-top: 8px">JSON 语法：{{ jsonError }}</div>
          <div v-else-if="dirty" class="notice notice-warn" style="margin-top: 8px">
            保存后会写入文件并提示：<b>修改配置通常需要重启对应服务才会生效</b>。原文件已备份到 backups 目录。
          </div>
          <div v-if="lastBackup" class="notice notice-ok" style="margin-top: 8px">最近一次备份：{{ lastBackup }}</div>
          <div class="dim mono" style="font-size: 11px; margin-top: 8px">{{ path }}</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <span>🗄</span>
        <h2>备份与还原</h2>
        <div class="card-actions">
          <button class="btn btn-sm btn-ghost" @click="loadBackups">刷新</button>
        </div>
      </div>
      <div v-if="!backups.length" class="empty">还没有备份。每次保存配置都会自动生成一份。</div>
      <table v-else class="table">
        <thead>
          <tr><th>备份文件</th><th style="width: 100px">大小</th><th style="width: 160px">时间</th><th style="width: 90px">操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in backups" :key="b.name">
            <td class="mono" style="font-size: 11.5px">{{ b.name }}</td>
            <td>{{ b.size_mb ?? '—' }}</td>
            <td class="mono" style="font-size: 11.5px">{{ b.modified_at || '—' }}</td>
            <td><button class="btn btn-sm btn-ghost" @click="restore(b.name)">还原</button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
