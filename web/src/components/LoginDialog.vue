<script setup lang="ts">
import { ref, watch } from 'vue'
import { NButton, NForm, NFormItem, NInput, NModal, useMessage } from 'naive-ui'
import { useAuth } from '../stores/auth'

const auth = useAuth()
const msg = useMessage()
const username = ref('')
const password = ref('')
const busy = ref(false)
const error = ref('')

watch(
  () => auth.showLogin,
  (v) => {
    if (v) {
      error.value = ''
      void auth.loadConfig()
    }
  },
)

function fill(u: string) {
  username.value = u
  password.value = auth.config.password ?? ''
}

async function submit() {
  if (!username.value || !password.value) return
  busy.value = true
  error.value = ''
  try {
    await auth.login(username.value, password.value)
    msg.success(`已登录：${auth.user?.name}（${auth.user?.role_cn}）`)
    password.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <n-modal v-model:show="auth.showLogin" preset="card" title="登录" style="width: 380px" :mask-closable="!busy">
    <n-form @submit.prevent="submit">
      <n-form-item label="用户名">
        <n-input v-model:value="username" placeholder="用户名" :input-props="{ autocomplete: 'username' }" />
      </n-form-item>
      <n-form-item label="口令">
        <n-input
          v-model:value="password"
          type="password"
          show-password-on="click"
          placeholder="口令"
          :input-props="{ autocomplete: 'current-password' }"
        />
      </n-form-item>
      <div v-if="error" class="err">{{ error }}</div>
      <n-button type="primary" block :loading="busy" attr-type="submit">登录</n-button>
    </n-form>

    <div v-if="auth.config.demo" class="demo">
      <div class="dim">演示账号（口令 {{ auth.config.password }}），点击填入：</div>
      <div class="accounts">
        <button v-for="a in auth.config.accounts" :key="a.username" class="acc" @click="fill(a.username)">
          {{ a.name }} <span class="dim">{{ a.username }} · {{ a.role_cn }}</span>
        </button>
      </div>
      <div class="dim small">遥控需要"调度员"操作 + "监护员"确认两个账号；未登录为访客只读。</div>
    </div>
  </n-modal>
</template>

<style scoped>
.err {
  color: var(--alarm);
  font-size: 13px;
  margin-bottom: 10px;
}
.demo {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--border);
  font-size: 13px;
}
.accounts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
  margin: 8px 0;
}
.acc {
  text-align: left;
  background: #16213a;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text);
  padding: 6px 8px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
}
.acc:hover {
  border-color: var(--accent);
}
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
}
</style>
