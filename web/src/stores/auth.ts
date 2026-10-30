import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, setToken, setUnauthorizedHandler } from '../api/client'
import type { AuthConfig, User } from '../api/types'

const KEY = 'scada.auth'

/** JWT 载荷中的过期时间（秒）；仅用于本地判断是否需要重新登录，真正校验在服务端 */
function tokenExp(token: string): number {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    return Number(payload.exp) || 0
  } catch {
    return 0
  }
}

export const useAuth = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const token = ref<string | null>(null)
  const showLogin = ref(false)
  const config = ref<AuthConfig>({ demo: false })

  function persist() {
    try {
      if (token.value && user.value) localStorage.setItem(KEY, JSON.stringify({ token: token.value, user: user.value }))
      else localStorage.removeItem(KEY)
    } catch {
      /* 隐私模式等场景下存储不可用：仅本次会话有效 */
    }
  }

  function restore() {
    try {
      const raw = localStorage.getItem(KEY)
      if (!raw) return
      const saved = JSON.parse(raw) as { token: string; user: User }
      if (tokenExp(saved.token) * 1000 > Date.now() + 60_000) {
        token.value = saved.token
        user.value = saved.user
        setToken(saved.token)
      } else localStorage.removeItem(KEY)
    } catch {
      /* 忽略 */
    }
  }

  async function login(username: string, password: string) {
    const r = await api.login(username, password)
    token.value = r.token
    user.value = r.user
    setToken(r.token)
    persist()
    showLogin.value = false
  }

  function logout() {
    token.value = null
    user.value = null
    setToken(null)
    persist()
  }

  async function loadConfig() {
    try {
      config.value = await api.authConfig()
    } catch {
      /* 后端不可用时登录框仍可用 */
    }
  }

  const can = (perm: string) => !!user.value?.perms.includes(perm)
  const loggedIn = computed(() => !!user.value)

  restore()
  setUnauthorizedHandler(() => {
    logout()
    showLogin.value = true
  })

  return { user, token, showLogin, config, loggedIn, can, login, logout, loadConfig }
})
