import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'
import { api } from '../api/client'
import type { Point, Station } from '../api/types'

/** 厂站模型与点表（启动时加载一次） */
export const useModel = defineStore('model', () => {
  const station = shallowRef<Station | null>(null)
  const points = shallowRef<Point[]>([])
  const error = ref<string | null>(null)
  let loading: Promise<void> | null = null

  const byId = computed(() => new Map(points.value.map((p) => [p.id, p])))
  const byBay = computed(() => {
    const m = new Map<string, Point[]>()
    for (const p of points.value) {
      if (!m.has(p.bay)) m.set(p.bay, [])
      m.get(p.bay)!.push(p)
    }
    return m
  })

  function load() {
    loading ??= Promise.all([api.station(), api.points()])
      .then(([s, p]) => {
        station.value = s
        points.value = p
        error.value = null
      })
      .catch((e) => {
        error.value = (e as Error).message
        loading = null
      })
    return loading
  }

  return { station, points, error, byId, byBay, load }
})
