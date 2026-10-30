import { createRouter, createWebHistory } from 'vue-router'

export const nav = [
  { path: '/diagram', title: '接线图', component: () => import('./views/DiagramView.vue') },
  { path: '/realtime', title: '实时数据', component: () => import('./views/RealtimeView.vue') },
  { path: '/alarms', title: '告警', component: () => import('./views/AlarmsView.vue') },
  { path: '/events', title: 'SOE 事件', component: () => import('./views/EventsView.vue') },
  { path: '/frames', title: '报文监视', component: () => import('./views/FramesView.vue') },
  { path: '/audit', title: '审计', component: () => import('./views/AuditView.vue') },
  { path: '/status', title: '系统状态', component: () => import('./views/StatusView.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/diagram' },
    ...nav.map((n) => ({ path: n.path, component: n.component, meta: { title: n.title } })),
    { path: '/:pathMatch(.*)*', redirect: '/diagram' },
  ],
})

router.afterEach((to) => {
  document.title = `${(to.meta.title as string) ?? ''} · PowerGrid SCADA`
})
