<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { darkTheme, NConfigProvider, NMessageProvider, type GlobalThemeOverrides } from 'naive-ui'
import { nav } from './router'
import { useRealtime } from './stores/realtime'

const rt = useRealtime()
const clock = ref('')
let timer: number | undefined

const pad = (n: number) => String(n).padStart(2, '0')
function updateClock() {
  // 显示服务器时间（已做时钟偏差修正），对应调度系统的"系统时钟"
  const d = rt.serverNow()
  clock.value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

onMounted(() => {
  rt.start()
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
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides">
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
          </div>
        </header>

        <main class="content">
          <router-view />
        </main>
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
.clock {
  font-size: 14px;
  color: var(--text);
}
.content {
  flex: 1;
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
  .link-text {
    display: none;
  }
  .nav-item {
    padding: 6px 8px;
  }
}
</style>
