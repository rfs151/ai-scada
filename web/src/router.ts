import { createRouter, createWebHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/status' },
    {
      path: '/status',
      name: 'status',
      meta: { title: '系统状态' },
      component: () => import('./views/StatusView.vue'),
    },
    { path: '/:pathMatch(.*)*', redirect: '/status' },
  ],
})

router.afterEach((to) => {
  document.title = `${(to.meta.title as string) ?? ''} · PowerGrid SCADA`
})
