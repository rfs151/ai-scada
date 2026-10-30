<script setup lang="ts">
/** 事故简报：规则模板即时生成；配置了大模型凭据时可由大模型基于同一份事实重写。 */
import { ref, watch } from 'vue'
import { NButton, NDrawer, NDrawerContent, NTag, useMessage } from 'naive-ui'
import { api } from '../../api/client'
import type { PdrBrief } from '../../api/types'
import { fmtTime } from '../../lib/format'
import { useAuth } from '../../stores/auth'

const props = defineProps<{ pdrId: number | null }>()
const show = defineModel<boolean>('show', { required: true })
const auth = useAuth()
const msg = useMessage()
const brief = ref<PdrBrief | null>(null)
const busy = ref<'' | 'load' | 'template' | 'llm'>('')

watch(
  () => [show.value, props.pdrId] as const,
  async ([s, id]) => {
    if (!s || !id) return
    busy.value = 'load'
    try {
      brief.value = await api.pdrBrief(id)
    } catch (e) {
      msg.error((e as Error).message)
    } finally {
      busy.value = ''
    }
  },
  { immediate: true },
)

async function regen(llm: boolean) {
  if (!props.pdrId) return
  busy.value = llm ? 'llm' : 'template'
  try {
    brief.value = await api.pdrBriefRegen(props.pdrId, llm)
  } catch (e) {
    msg.error((e as Error).message)
  } finally {
    busy.value = ''
  }
}

async function copy() {
  if (!brief.value) return
  try {
    await navigator.clipboard.writeText(brief.value.text)
    msg.success('已复制')
  } catch {
    msg.error('复制失败，请手动选择文本')
  }
}
</script>

<template>
  <n-drawer v-model:show="show" :width="560" placement="right">
    <n-drawer-content title="事故简报" closable :native-scrollbar="false">
      <div v-if="busy === 'load'" class="dim">生成中…</div>
      <template v-else-if="brief">
        <div class="meta">
          <n-tag size="small" :type="brief.source === 'llm' ? 'info' : 'default'">
            {{ brief.source === 'llm' ? `大模型撰写 · ${brief.model}` : '规则模板' }}
          </n-tag>
          <span class="dim small">{{ fmtTime(brief.at, true) }}</span>
        </div>
        <pre class="text">{{ brief.text }}</pre>
        <div class="dim small note">
          事实（动作序列、开关变位、失电负荷、故障电流）均从追忆数据中提取；大模型只拿到这份事实，不接触原始数据。
        </div>
      </template>
    </n-drawer-content>
    <template #footer>
      <div class="foot">
        <n-button size="small" :disabled="!brief" @click="copy">复制</n-button>
        <n-button size="small" :loading="busy === 'template'" :disabled="!auth.loggedIn" @click="regen(false)">
          按规则重新生成
        </n-button>
        <n-button
          size="small"
          type="primary"
          :loading="busy === 'llm'"
          :disabled="!auth.loggedIn || !brief?.llm_available"
          :title="!brief?.llm_available ? '服务端未配置 ANTHROPIC_API_KEY' : !auth.loggedIn ? '请先登录' : ''"
          @click="regen(true)"
        >
          大模型撰写
        </n-button>
      </div>
    </template>
  </n-drawer>
</template>

<style scoped>
.meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
.text {
  white-space: pre-wrap;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.7;
  margin: 0;
  padding: 12px;
  background: #0f172a;
  border-radius: 4px;
}
.note {
  margin-top: 10px;
}
.foot {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
