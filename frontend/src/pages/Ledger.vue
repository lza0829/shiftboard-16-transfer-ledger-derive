<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const flows = ref([])
const period = ref({ start: '', end: '' })
const start = ref('')
const end = ref('')
const rows = ref([])
const drill = ref(null)
const drillId = ref(null)
const err = ref('')

function fmt(d) {
  return d.toISOString().slice(0, 10)
}

async function loadFlows() {
  const q = []
  if (start.value) q.push(`start=${start.value}`)
  if (end.value) q.push(`end=${end.value}`)
  const data = await getJson('/api/ledger/flows' + (q.length ? `?${q.join('&')}` : ''))
  flows.value = data.rows
  period.value = { start: data.start, end: data.end }
}

async function loadLedger() {
  const q = drillId.value ? `?handover_id=${drillId.value}` : ''
  rows.value = await getJson('/api/ledger' + q)
}

async function refresh() {
  err.value = ''
  try {
    await Promise.all([loadFlows(), loadLedger()])
  } catch (e) {
    err.value = String(e.message || e)
  }
}

function setToday() {
  start.value = ''
  end.value = ''
  loadFlows()
}

function setLast7() {
  const now = new Date()
  const s = new Date(now.getTime() - 6 * 86400000)
  const e = new Date(now.getTime() + 86400000)
  start.value = fmt(s)
  end.value = fmt(e)
  loadFlows()
}

function setAll() {
  start.value = '2000-01-01'
  end.value = '2100-01-01'
  loadFlows()
}

async function drillHandover(id) {
  err.value = ''
  try {
    drill.value = await getJson(`/api/handovers/${id}`)
    drillId.value = id
    await loadLedger()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function clearDrill() {
  drill.value = null
  drillId.value = null
  await loadLedger()
}

onMounted(refresh)
</script>

<template>
  <div>
    <h1>流转账本</h1>
    <p class="lead">
      每笔过户/回拨与归属变更同一事务落账本，账本只追加；交入/交出/净流量由账本查询时聚合派生，在途数以批次表为准。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>

    <div class="panel">
      <h2>时段净流量（账本派生）</h2>
      <div class="row">
        <label>起始（含）
          <input type="date" v-model="start" @change="loadFlows" />
        </label>
        <label>截止（不含）
          <input type="date" v-model="end" @change="loadFlows" />
        </label>
        <button class="btn ghost" @click="setToday">今日</button>
        <button class="btn ghost" @click="setLast7">近7天</button>
        <button class="btn ghost" @click="setAll">全部</button>
      </div>
      <p class="lead" style="margin-bottom:12px">
        当前时段：{{ period.start }} → {{ period.end }}（截止不含）
      </p>
      <table>
        <thead>
          <tr><th>班次</th><th>交入</th><th>交出</th><th>净流量</th></tr>
        </thead>
        <tbody>
          <tr v-for="f in flows" :key="f.shift_id">
            <td>{{ f.shift_name }}</td>
            <td>{{ f.交入 }}</td>
            <td>{{ f.交出 }}</td>
            <td>
              <span class="tag" :class="{ on: f.净流量 !== 0 }">
                {{ f.净流量 > 0 ? '+' : '' }}{{ f.净流量 }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel" v-if="drill">
      <h2>
        单号 #{{ drill.id }} 下钻（与交班记录同源）
        <button class="btn ghost" style="margin-left:12px" @click="clearDrill">清除下钻</button>
      </h2>
      <p class="lead" style="margin-bottom:12px">
        {{ drill.from_shift_name }} → {{ drill.to_shift_name }} · {{ drill.status }} ·
        清单 {{ drill.batch_count }} 批 · {{ drill.started_at }} → {{ drill.finished_at || '—' }}
        <template v-if="drill.reason"> · {{ drill.reason }}</template>
      </p>
      <table>
        <thead>
          <tr><th>业务键</th><th>标题</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="b in drill.items || []" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.status }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>账本明细{{ drillId ? `（仅单号 #${drillId}）` : '' }}</h2>
      <table>
        <thead>
          <tr>
            <th>时间</th><th>单号</th><th>业务键</th>
            <th>交出方</th><th>交入方</th><th>方向</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.occurred_at }}</td>
            <td>
              <a href="#" class="link" @click.prevent="drillHandover(r.handover_id)">#{{ r.handover_id }}</a>
            </td>
            <td>{{ r.biz_key }}</td>
            <td>{{ r.from_shift_name }}</td>
            <td>{{ r.to_shift_name }}</td>
            <td>
              <span class="tag" :class="{ on: r.direction === '过户' }">{{ r.direction }}</span>
            </td>
          </tr>
          <tr v-if="!rows.length"><td colspan="6">暂无账本行</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
