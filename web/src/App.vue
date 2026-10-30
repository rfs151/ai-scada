<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { darkTheme, NConfigProvider, NMessageProvider, type GlobalThemeOverrides } from 'naive-ui'
import AlarmPanel from './components/AlarmPanel.vue'
import LoginDialog from './components/LoginDialog.vue'
import { unlockAudio } from './lib/sound'
import { nav } from './router'
import { useAuth } from './stores/auth'
import { useOps } from './stores/ops'
import { useRealtime } from './stores/realtime'

const rt = useRealtime()
const auth = useAuth()
const ops = useOps()
const clock = ref('')
let timer: number | undefined

const pad = (n: number) => String(n).padStart(2, '0')
function updateClock() {
  // 显示服务器时间（已做时钟偏差修正），对应调度系统的"系统时钟"
  const d = rt.serverNow()
  clock.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

onMounted(() => {
  ops.start() // 先注册推送监听，再建立连接
  rt.start()
  void auth.loadConfig()
  // 浏览器要求用户交互后才能播放声音：首次点击时解锁告警音响
  window.addEventListener('pointerdown', unlockAudio, { once: true })
  updateClock()
  timer = window.setInterval(updateClock, 250)
})
onBeforeUnmount(() => {
  window.clearInterval(timer)
  rt.stop()
})

const linkText = { open: '实时链路正常', connecting: '正在连接', closed: '链路中断' } as const

const themeOverrides: GlobalThemeOverrides = {
  common: {
    bodyColor: '#0b1220',
    cardColor: '#111a2e',
    borderColor: '#1f2b45',
    primaryColor: '#3b82f6',
    primaryColorHover: '#60a5fa',
    modalColor: '#111a2e',
    popoverColor: '#16213a',
  },
  Drawer: { color: '#0f172a' },
}

</script>

<template>
  <n-config-provider abstract :theme="darkTheme" :theme-overrides="themeOverrides">
    <n-message-provider>
      <div class="shell">
        <header class="topbar">
          <div class="brand">
            <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true">
              <path d="M18 3 7 18h7l-2 11 11-15h-7z" fill="var(--warn)" />
            </svg>
            <span class="title">110kV 示范变电站 · SCADA</span>
          </div>

          <nav class="nav">
            <router-link v-for="n in nav" :key="n.path" :to="n.path" class="nav-item">{{ n.title }}</router-link>
          </nav>

          <div class="status">
            <span class="link" :class="rt.state" :title="`重连 ${rt.reconnects} 次`">
              <i class="dot" /><span class="link-text">{{ linkText[rt.state] }}</span>
              <span v-if="rt.state === 'open' && rt.rttMs !== null" class="mono dim">{{ rt.rttMs }} ms</span>
            </span>
            <span class="mono clock">{{ clock }}</span>
            <span v-if="auth.user" class="user" :title="`${auth.user.username} · ${auth.user.role_cn}`">
              {{ auth.user.name }}<span class="role">{{ auth.user.role_cn }}</span>
              <button class="logout" @click="auth.logout()">退出</button>
            </span>
            <button v-else class="login" @click="auth.showLogin = true">登录</button>
          </div>
        </header>

        <main class="content">
          <router-view />
        </main>
        <AlarmPanel />
        <LoginDialog />
      </div>
    </n-message-provider>
  </n-config-provider>
</template>

<style scoped>
.shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.topbar {
  display: flex;
  align-items: center;
  gap: 24px;
  height: 48px;
  padding: 0 16px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  letter-spacing: 0.5px;
  white-space: nowrap;
}
.nav {
  display: flex;
  gap: 4px;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
}
.nav-item {
  white-space: nowrap;
  padding: 6px 12px;
  border-radius: 4px;
  color: var(--text-dim);
  text-decoration: none;
}
.nav-item.router-link-active {
  color: var(--text);
  background: #1b2742;
}
.status {
  display: flex;
  align-items: center;
  gap: 16px;
  font-size: 13px;
  white-space: nowrap;
}
.link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--deenergized);
}
.link.open .dot {
  background: var(--ok);
  box-shadow: 0 0 6px var(--ok);
}
.link.connecting .dot {
  background: var(--warn);
  animation: blink 1s infinite;
}
.link.closed {
  color: var(--alarm);
}
.link.closed .dot {
  background: var(--alarm);
  animation: blink 0.5s infinite;
}
.dim {
  color: var(--text-dim);
}
.user {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.role {
  font-size: 11px;
  padding: 0 6px;
  border-radius: 3px;
  background: #1b2742;
  color: var(--text-dim);
}
.login,
.logout {
  background: none;
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 4px;
  padding: 2px 10px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.logout {
  color: var(--text-dim);
}
.login:hover,
.logout:hover {
  border-color: var(--accent);
}
.clock {
  font-size: 14px;
  color: var(--text);
}
.content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px;
}
@keyframes blink {
  50% {
    opacity: 0.2;
  }
}
@media (max-width: 720px) {
  .topbar {
    gap: 12px;
    padding: 0 12px;
  }
  .title,
  .clock,
  .link-text,
  .role {
    display: none;
  }
  .nav-item {
    padding: 6px 8px;
  }
}
</style>
