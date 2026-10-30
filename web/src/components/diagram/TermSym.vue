<script setup lang="ts">
/** 端子图元：电源（上级变电站）、负荷、光伏、电容器、连接点 */
defineProps<{ kind: string; x: number; y: number; color: string; label?: string }>()
</script>

<template>
  <g :transform="`translate(${x} ${y})`">
    <template v-if="kind === 'source'">
      <path :stroke="color" class="w" d="M0 0V-12" />
      <path :fill="color" d="M-6 -10L0 -20L6 -10Z" />
      <text class="src" y="-28" text-anchor="middle">{{ label }}</text>
    </template>

    <path v-else-if="kind === 'load'" :fill="color" d="M-7 0H7L0 14Z" />

    <g v-else-if="kind === 'pv'">
      <rect x="-16" y="0" width="32" height="26" rx="2" :stroke="color" class="w" fill="var(--bg)" />
      <path :stroke="color" class="thin" d="M-16 26L16 0M-12 6H-4M-12 9H-4M3 19q2.5 -3 5 0t5 0" />
    </g>

    <g v-else-if="kind === 'capacitor'">
      <path :stroke="color" class="w" d="M-14 0H14M-14 8H14M0 8V20" />
      <path class="ground" d="M-9 20H9M-6 25H6M-3 30H3" />
    </g>

    <circle v-else-if="kind === 'dot'" r="3.5" :fill="color" />
  </g>
</template>

<style scoped>
.w {
  fill: none;
  stroke-width: 2.5;
}
.thin {
  fill: none;
  stroke-width: 1.2;
}
.ground {
  fill: none;
  stroke: var(--text-dim);
  stroke-width: 1.6;
}
.src {
  font-size: 12px;
  fill: var(--text-dim);
}
</style>
