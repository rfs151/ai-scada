import { createRouter, createWebHistory } from 'vue-router'

export const nav = [
  { path: '/realtime', title: '实时数据', component: () => import('./views/RealtimeView.vue') },
  { path: '/events', title: 'SOE 事件', component: () => import('./views/EventsView.vue') },
  { path: '/frames', title: '报文监视', component: () => import('./views/FramesView.vue') },
  { path: '/status', title: '系统状态', component: () => import('./views/StatusView.vue') },
]

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/realtime' },
    ...nav.map((n) => ({ path: n.path, component: n.component, meta: { title: n.title } })),
    { path: '/:pathMatch(.*)*', redirect: '/realtime' },
  ],
})

router.afterEach((to) => {
  document.title = `${(to.meta.title as string) ?? ''} · PowerGrid SCADA`
})
