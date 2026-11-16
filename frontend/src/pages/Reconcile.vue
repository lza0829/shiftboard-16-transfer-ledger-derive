<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const runs = ref([])
const diffs = ref([])
const sampleSize = ref(10)
const err = ref('')
const msg = ref('')
const ok = ref(false)

async function loadAudit() {
  const [r, d] = await Promise.all([
    getJson('/api/reconcile/runs'),
    getJson('/api/reconcile/diffs'),
  ])
  runs.value = r
  diffs.value = d
}

async function runAudit() {
  msg.value = ''
  ok.value = false
  try {
    const r = await postJson('/api/reconcile/run', { sample_size: Number(sampleSize.value) })
    ok.value = r.diff_count === 0
    msg.value = r.diff_count === 0
      ? `抽查 ${r.checked_count} 行账本，全部能在在途/结清/再交链上解释`
      : `抽查 ${r.checked_count} 行账本，${r.diff_count} 个业务键解释不了，已物化差异`
    await loadAudit()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

onMounted(async () => {
  try {
    rows.value = await getJson('/api/reconcile')
    await loadAudit()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>交班对账</h1>
    <p class="lead">交班前后：交班方在途应变 0，接班方在途应接住清单批次数；数字与交班单同源。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>报告</th><th>交班单</th><th>交班前清单</th>
            <th>交后交班方</th><th>交后接班方</th><th>一致</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>#{{ r.id }}</td>
            <td>#{{ r.handover_id }}</td>
            <td>{{ r.before_count }}</td>
            <td>{{ r.after_from_count }}</td>
            <td>{{ r.after_to_count }}</td>
            <td>
              <span class="tag" :class="{ on: r.一致 }">{{ r.一致 ? '一致' : '不一致' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>抽查对账（账本链 → 批次表）</h2>
      <p class="lead" style="margin-bottom:12px">
        抽查账本过户行，沿业务键追到链尾，核对能否由接班方在途、后续结清或再交链解释；
        解释不了的业务键物化进差异表，只追加、禁止删改账本抹平。
      </p>
      <div class="row">
        <label>抽查行数
          <input type="number" min="1" max="1000" v-model.number="sampleSize" />
        </label>
        <button class="btn" @click="runAudit">运行抽查</button>
      </div>
      <p class="msg" :class="{ ok }">{{ msg }}</p>
      <table>
        <thead>
          <tr><th>运行</th><th>时间</th><th>抽查行数</th><th>差异数</th></tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td>#{{ r.id }}</td>
            <td>{{ r.created_at }}</td>
            <td>{{ r.checked_count }}</td>
            <td>
              <span class="tag" :class="{ on: r.diff_count === 0 }">{{ r.diff_count }}</span>
            </td>
          </tr>
          <tr v-if="!runs.length"><td colspan="4">暂无抽查运行</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>对账差异（物化，只追加）</h2>
      <table>
        <thead>
          <tr>
            <th>差异</th><th>运行</th><th>业务键</th><th>链尾单号</th>
            <th>应归属</th><th>实际归属</th><th>原因</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in diffs" :key="d.id">
            <td>#{{ d.id }}</td>
            <td>#{{ d.run_id }}</td>
            <td>{{ d.biz_key }}</td>
            <td>#{{ d.handover_id }}</td>
            <td>{{ d.expected_shift_name || d.expected_shift_id }}</td>
            <td>{{ d.actual_shift_name || '—' }}</td>
            <td>{{ d.reason }}</td>
            <td>{{ d.created_at }}</td>
          </tr>
          <tr v-if="!diffs.length"><td colspan="8">暂无差异</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
