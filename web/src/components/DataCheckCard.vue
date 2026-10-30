<script setup lang="ts">
/**
 * 遥测数据校核：利用量测冗余（功率平衡、S = √3·U·I）发现坏数据。
 * 与告警引擎同一份计算结果，每 2 秒更新一次。
 */
import { NCard, NTag } from 'naive-ui'
import type { DataCheck } from '../api/types'

defineProps<{ data: DataCheck | null }>()
const f2 = (v: number) => (Math.abs(v) < 0.005 ? 0 : v).toFixed(2)
</script>

<template>
  <n-card size="small" class="dc">
    <template #header>
      数据校核
      <span class="dim small"
        >利用量测冗余发现坏数据：节点功率平衡（基尔霍夫定律）+ 间隔自洽（S =
        √3·U·I），容差计入上送死区</span
      >
    </template>
    <template #header-extra>
      <n-tag
        v-if="data"
        size="small"
        round
        :type="data.issues.length ? 'warning' : 'success'"
      >
        {{ data.issues.length ? `${data.issues.length} 项可疑` : "量测一致" }}
      </n-tag>
    </template>
    <div v-if="!data?.t" class="dim">等待首次校核…</div>
    <template v-else>
      <div
        v-for="i in data.issues"
        :key="i.key"
        class="issue"
        :class="{ pending: !i.active }"
      >
        <n-tag size="small" :type="i.active ? 'warning' : 'default'">{{
          i.active ? "已告警" : "观察中"
        }}</n-tag>
        <span>{{ i.text }}</span>
      </div>
      <div class="cols">
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>功率平衡</th>
                <th>流入 MW</th>
                <th>流出 MW</th>
                <th>差</th>
                <th>容差</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="b in data.balances"
                :key="b.key"
                :class="{ bad: !b.ok }"
              >
                <td>{{ b.name }}</td>
                <td class="mono">{{ f2(b.in) }}</td>
                <td class="mono">{{ f2(b.out) }}</td>
                <td class="mono">{{ f2(b.diff) }}</td>
                <td class="mono dim">±{{ f2(b.tol) }}</td>
                <td>{{ b.ok ? "✔" : "✘" }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>间隔自洽</th>
                <th>√(P²+Q²)</th>
                <th>√3·U·I</th>
                <th>偏差</th>
                <th />
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="b in data.bays"
                :key="b.bay"
                :class="{ bad: !b.ok || !b.sign_ok }"
              >
                <td>{{ b.name }}</td>
                <td class="mono">{{ f2(b.s_pq) }}</td>
                <td class="mono">{{ f2(b.s_ui) }}</td>
                <td class="mono">{{ b.dev_pct.toFixed(1) }}%</td>
                <td>{{ !b.sign_ok ? "方向异常" : b.ok ? "✔" : "✘" }}</td>
              </tr>
            </tbody>
          </table>
          <div class="dim small">
            停运间隔、电流 &lt; 10A 或品质无效的量测不参与校核
          </div>
        </div>
      </div>
    </template>
  </n-card>
</template>

<style scoped>
.dim {
  color: var(--text-dim);
}
.small {
  font-size: 12px;
  font-weight: normal;
}
.issue {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 8px;
  margin-bottom: 6px;
  border-radius: 4px;
  background: rgba(245, 158, 11, 0.12);
  font-size: 13px;
}
.issue.pending {
  background: rgba(148, 163, 184, 0.1);
}
.cols {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: 16px;
}
.tbl-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
th,
td {
  text-align: left;
  padding: 4px 8px;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
th {
  color: var(--text-dim);
  font-weight: normal;
}
tr.bad td {
  color: var(--warn, #f59e0b);
}
@media (max-width: 720px) {
  .cols {
    grid-template-columns: 1fr;
  }
}
</style>
