<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  BarChart3,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Database,
  Download,
  Filter,
  Loader2,
  Package,
  RefreshCw,
  Search,
  ShoppingCart,
  Store,
  Table2,
  TrendingUp,
} from '@lucide/vue'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'
// 分页策略改版后使用新 key，避免沿用旧版本中可能保存的 maxPages=1。
const FILTERS_KEY = 'wdt_order_analysis_filters_v2'

const platformOptions = [
  { id: 'all', name: '全部平台' },
  { id: '39', name: '拼多多' },
  { id: '1', name: '淘宝 / 天猫' },
  { id: '3', name: '京东' },
  { id: '127', name: '抖音' },
]

const timeOptions = [
  { value: 1, label: '最后修改时间 modified' },
  { value: 2, label: '下单时间 trade_time' },
  { value: 3, label: '创建时间 created' },
]

const fieldLabels = {
  'trade.trade_no': '订单号',
  'trade.trade_id': '订单 ID',
  'trade.shop_name': '店铺',
  'trade.platform_id': '平台 ID',
  'trade.trade_time': '交易时间',
  'trade.created': '创建时间',
  'trade.trade_status': '订单状态',
  'trade.real_amount': '订单金额',
  'trade.receivable': '应收金额',
  'trade.paid': '已付金额',
  'trade.goods_count': '商品数',
  'trade.logistics_name': '物流公司',
  'trade.logistics_no': '物流单号',
  'goods.goods_no': '货品编码',
  'goods.goods_name': '商品名称',
  'goods.spec_name': '规格',
  'goods.num': '购买数量',
  'goods.price': '商品单价',
  'goods.paid': '商品实付',
  'goods.refund_status': '退款状态',
  'logistics.logistics_name': '物流公司',
  'logistics.logistics_no': '物流单号',
}

const preferredColumns = [
  'trade.trade_no',
  'trade.shop_name',
  'trade.trade_time',
  'goods.goods_no',
  'goods.goods_name',
  'goods.spec_name',
  'goods.num',
  'goods.paid',
  'trade.real_amount',
  'trade.trade_status',
  'logistics.logistics_name',
  'logistics.logistics_no',
]

function toDateInput(date) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function defaultFilters() {
  const end = new Date()
  const start = new Date(end)
  start.setDate(start.getDate() - 2)
  return {
    startAt: `${toDateInput(start)}T00:00`,
    endAt: `${toDateInput(end)}T23:59`,
    platformId: '39',
    timeType: 1,
    pageSize: 100,
    maxPages: null,
    exportAfterQuery: false,
  }
}

const filters = reactive(defaultFilters())
const result = ref(null)
const loading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const detailKeyword = ref('')
const detailPage = ref(1)
const lastQueriedAt = ref('')
const PREVIEW_PAGE_SIZE = 120

function loadFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY) || 'null')
    if (saved && typeof saved === 'object') Object.assign(filters, saved)
  } catch {
    localStorage.removeItem(FILTERS_KEY)
  }
}

function saveFilters() {
  localStorage.setItem(FILTERS_KEY, JSON.stringify({ ...filters }))
}

function resetFilters() {
  Object.assign(filters, defaultFilters())
  saveFilters()
  result.value = null
  errorMessage.value = ''
  successMessage.value = ''
}

function toApiDateTime(value, endOfMinute = false) {
  return value.length === 16 ? `${value.replace('T', ' ')}:${endOfMinute ? '59' : '00'}` : value.replace('T', ' ')
}

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`)
  return data
}

async function queryOrders() {
  errorMessage.value = ''
  successMessage.value = ''
  detailKeyword.value = ''

  if (!filters.startAt || !filters.endAt || filters.startAt >= filters.endAt) {
    errorMessage.value = '开始时间必须早于结束时间。'
    return
  }

  saveFilters()
  loading.value = true
  try {
    const data = await fetchJson(`${API_BASE_URL}/api/wdt/orders/query`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        start_time: toApiDateTime(filters.startAt),
        end_time: toApiDateTime(filters.endAt, true),
        platform_ids: filters.platformId === 'all' ? [] : [filters.platformId],
        page_size: Number(filters.pageSize),
        time_type: Number(filters.timeType),
        // 留空表示由后端依据 total_count 自动拉取完整分页。
        max_pages: filters.maxPages ? Number(filters.maxPages) : null,
      }),
    })
    result.value = data
    lastQueriedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    successMessage.value = data.order_count
      ? `已完整加载 ${formatNumber(data.order_count)} 笔订单，接口分页 ${formatNumber(data.page_count)} 页，覆盖 ${data.source_window_count} 个日窗口。`
      : '查询完成，当前时间范围没有匹配订单。'
    if (filters.exportAfterQuery && data.rows?.length) downloadCsv(data)
  } catch (error) {
    result.value = null
    errorMessage.value = error.message || '查询失败，请稍后重试。'
  } finally {
    loading.value = false
  }
}

function formatNumber(value, digits = 0) {
  const number = Number(value || 0)
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(number)
}

function formatMoney(value) {
  return `¥${formatNumber(value, 2)}`
}

function formatUnits(value) {
  return formatNumber(value, Number(value) % 1 ? 2 : 0)
}

function formatDateTime(value) {
  if (!value) return '-'
  return String(value).replace('T', ' ').replace(/:00$/, '')
}

function formatCell(column, value) {
  if (value === null || value === undefined || value === '') return '-'
  if (column.includes('amount') || column.endsWith('.paid') || column.endsWith('.price')) {
    const number = Number(value)
    return Number.isFinite(number) ? formatMoney(number) : String(value)
  }
  if (column.includes('time') || column.endsWith('.created')) return formatDateTime(value)
  return String(value)
}

function columnLabel(column) {
  return fieldLabels[column] || column.replace('trade.', '订单 · ').replace('goods.', '商品 · ').replace('logistics.', '物流 · ')
}

const summary = computed(() => result.value?.summary || {})
const tableColumns = computed(() => {
  const columns = result.value?.columns || []
  const selected = preferredColumns.filter((column) => columns.includes(column))
  return (selected.length ? selected : columns).slice(0, 12)
})
const filteredDetailRows = computed(() => {
  const rows = result.value?.rows || []
  const keyword = detailKeyword.value.trim().toLowerCase()
  return keyword
    ? rows.filter((row) => tableColumns.value.some((column) => String(row[column] ?? '').toLowerCase().includes(keyword)))
    : rows
})
const detailPageCount = computed(() => Math.max(1, Math.ceil(filteredDetailRows.value.length / PREVIEW_PAGE_SIZE)))
const filteredRows = computed(() => {
  const safePage = Math.min(detailPage.value, detailPageCount.value)
  const start = (safePage - 1) * PREVIEW_PAGE_SIZE
  return filteredDetailRows.value.slice(start, start + PREVIEW_PAGE_SIZE)
})
const hasOrders = computed(() => Number(summary.value.order_count || 0) > 0)
const maxShopAmount = computed(() => Math.max(...(result.value?.shops || []).map((item) => Number(item.order_amount || 0)), 1))
const maxProductAmount = computed(() => Math.max(...(result.value?.products || []).map((item) => Number(item.order_amount || 0)), 1))

watch([detailKeyword, result], () => {
  detailPage.value = 1
})
watch(detailPageCount, (pageCount) => {
  if (detailPage.value > pageCount) detailPage.value = pageCount
})

const dailyChart = computed(() => {
  const data = result.value?.daily || []
  const width = 720
  const height = 240
  const padX = 34
  const padY = 26
  const max = Math.max(...data.map((item) => Number(item.order_amount || 0)), 1)
  const step = data.length > 1 ? (width - padX * 2) / (data.length - 1) : 0
  const points = data.map((item, index) => {
    const amount = Number(item.order_amount || 0)
    const x = data.length > 1 ? padX + step * index : width / 2
    const y = height - padY - (amount / max) * (height - padY * 2)
    return { ...item, x, y, amount }
  })
  return { width, height, max, points, polyline: points.map((point) => `${point.x},${point.y}`).join(' ') }
})

function barWidth(value, max) {
  return `${Math.max(4, Math.min(100, (Number(value || 0) / max) * 100))}%`
}

function shopShare(amount) {
  return summary.value.order_amount ? `${formatNumber((Number(amount || 0) / summary.value.order_amount) * 100, 1)}%` : '0%'
}

function downloadCsv(data = result.value) {
  if (!data?.columns?.length) return
  const escapeCsv = (value) => {
    const text = value === null || value === undefined ? '' : String(value)
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
  }
  const lines = [data.columns.map(escapeCsv).join(',')]
  for (const row of data.rows || []) lines.push(data.columns.map((column) => escapeCsv(row[column])).join(','))
  const blob = new Blob([`\ufeff${lines.join('\n')}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `wdt_orders_${filters.startAt.slice(0, 10)}_${filters.endAt.slice(0, 10)}.csv`
  link.click()
  URL.revokeObjectURL(url)
}

onMounted(loadFilters)
</script>

<template>
  <main class="app-shell">
    <header class="app-header">
      <div class="brand-block">
        <div class="brand-mark"><BarChart3 :size="22" /></div>
        <div>
          <strong>旺店通订单分析</strong>
          <span>订单、店铺、商品的近三天经营视图</span>
        </div>
      </div>
      <div class="header-status">
        <span class="status-dot"></span>
        <Database :size="15" />
        <span>旺店通 OpenAPI</span>
        <span v-if="lastQueriedAt" class="status-time">最近查询 {{ lastQueriedAt }}</span>
      </div>
    </header>

    <section class="query-panel">
      <div class="section-heading compact-heading">
        <div class="heading-icon"><Filter :size="18" /></div>
        <div>
          <h1>查询条件</h1>
          <p>接口按自然日自动拆分请求，适合查看近三天订单与经营变化。</p>
        </div>
      </div>

      <form class="query-form" @submit.prevent="queryOrders">
        <label>
          <span>开始时间</span>
          <div class="input-with-icon"><CalendarDays :size="16" /><input v-model="filters.startAt" type="datetime-local" /></div>
        </label>
        <label>
          <span>结束时间</span>
          <div class="input-with-icon"><CalendarDays :size="16" /><input v-model="filters.endAt" type="datetime-local" /></div>
        </label>
        <label>
          <span>平台筛选</span>
          <select v-model="filters.platformId">
            <option v-for="option in platformOptions" :key="option.id" :value="option.id">{{ option.name }}</option>
          </select>
        </label>
        <label>
          <span>时间字段</span>
          <select v-model="filters.timeType">
            <option v-for="option in timeOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
          </select>
        </label>
        <label>
          <span>页大小</span>
          <input v-model.number="filters.pageSize" type="number" min="1" max="100" step="1" />
        </label>
        <label>
          <span>最大页数 / 日（可选）</span>
          <input v-model.number="filters.maxPages" type="number" min="1" max="1000" step="1" placeholder="自动拉取全部" />
        </label>
      </form>

      <div class="query-actions">
        <label class="check-field">
          <input v-model="filters.exportAfterQuery" type="checkbox" />
          <span>查询后自动导出 CSV</span>
        </label>
        <div class="action-group">
          <button class="secondary-button" type="button" :disabled="loading" @click="resetFilters"><RefreshCw :size="16" />重置</button>
          <button class="primary-button" type="button" :disabled="loading" @click="queryOrders">
            <Loader2 v-if="loading" class="spin" :size="17" />
            <Search v-else :size="17" />
            {{ loading ? '正在查询旺店通…' : '查询订单' }}
          </button>
        </div>
      </div>
    </section>

    <div v-if="errorMessage" class="message error"><CircleAlert :size="18" /><span>{{ errorMessage }}</span></div>
    <div v-if="successMessage" class="message success"><CheckCircle2 :size="18" /><span>{{ successMessage }}</span></div>

    <template v-if="result && hasOrders">
      <section class="result-header">
        <div>
          <span class="eyebrow">查询结果</span>
          <h2>近三天经营概览</h2>
          <p>{{ formatDateTime(result.start_time) }} 至 {{ formatDateTime(result.end_time) }} · {{ result.platform_ids.length ? result.platform_ids.map((id) => platformOptions.find((item) => item.id === id)?.name || id).join('、') : '全部平台' }}</p>
        </div>
        <button class="secondary-button" type="button" @click="downloadCsv()"><Download :size="17" />下载完整 CSV</button>
      </section>

      <section class="kpi-grid">
        <article class="kpi-card accent-blue"><div class="kpi-label"><ShoppingCart :size="17" />订单数</div><strong>{{ formatNumber(summary.order_count) }}</strong><span>明细行 {{ formatNumber(summary.detail_count) }}</span></article>
        <article class="kpi-card accent-green"><div class="kpi-label"><TrendingUp :size="17" />成交金额</div><strong>{{ formatMoney(summary.order_amount) }}</strong><span>客单价 {{ formatMoney(summary.avg_order_amount) }}</span></article>
        <article class="kpi-card accent-orange"><div class="kpi-label"><Package :size="17" />商品销量</div><strong>{{ formatUnits(summary.units) }}</strong><span>{{ formatNumber(summary.product_count) }} 个商品</span></article>
        <article class="kpi-card accent-purple"><div class="kpi-label"><Store :size="17" />店铺数</div><strong>{{ formatNumber(summary.shop_count) }}</strong><span>已按店铺聚合</span></article>
      </section>

      <section class="analysis-grid">
        <article class="panel trend-panel">
          <div class="panel-heading"><div><h3>时间趋势</h3><p>按交易时间汇总订单数与成交金额</p></div><span class="chart-note">{{ result.daily.length }} 天</span></div>
          <div class="trend-chart" :style="{ '--chart-max': dailyChart.max }">
            <div class="y-axis"><span>{{ formatMoney(dailyChart.max) }}</span><span>{{ formatMoney(dailyChart.max / 2) }}</span><span>¥0</span></div>
            <svg :viewBox="`0 0 ${dailyChart.width} ${dailyChart.height}`" preserveAspectRatio="none" role="img" aria-label="每日成交金额趋势">
              <line v-for="line in [0, 1, 2]" :key="line" x1="34" :y1="26 + line * 94" x2="686" :y2="26 + line * 94" class="grid-line" />
              <polyline :points="dailyChart.polyline" class="trend-line" />
              <g v-for="point in dailyChart.points" :key="point.date">
                <title>{{ point.date }}：{{ formatNumber(point.order_count) }} 单，{{ formatMoney(point.amount) }}</title>
                <circle :cx="point.x" :cy="point.y" r="5" class="trend-point" />
              </g>
            </svg>
            <div class="x-axis"><span v-for="point in dailyChart.points" :key="point.date">{{ point.date.slice(5) }}</span></div>
          </div>
          <div class="trend-footer"><span><i class="legend-dot blue"></i>成交金额</span><span><i class="legend-dot gray"></i>订单数 {{ formatNumber(summary.order_count) }} 单</span></div>
        </article>

        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>店铺排行</h3><p>按成交金额排序</p></div><Store :size="18" class="panel-icon" /></div>
          <div class="ranking-list">
            <div v-for="(shop, index) in result.shops.slice(0, 8)" :key="`${shop.shop_id}-${shop.shop_name}`" class="ranking-row">
              <b>{{ index + 1 }}</b><div class="ranking-main"><strong :title="shop.shop_name">{{ shop.shop_name }}</strong><div class="bar-track"><i :style="{ width: barWidth(shop.order_amount, maxShopAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatMoney(shop.order_amount) }}</strong><span>{{ formatNumber(shop.order_count) }} 单 · {{ shopShare(shop.order_amount) }}</span></div>
            </div>
            <div v-if="!result.shops.length" class="mini-empty">暂无店铺数据</div>
          </div>
        </article>

        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>商品排行</h3><p>按商品行金额排序</p></div><Package :size="18" class="panel-icon" /></div>
          <div class="ranking-list">
            <div v-for="(product, index) in result.products.slice(0, 8)" :key="`${product.product_no}-${product.spec_name}`" class="ranking-row">
              <b>{{ index + 1 }}</b><div class="ranking-main"><strong :title="product.product_name">{{ product.product_name }}</strong><small>{{ product.product_no || '无货号' }}<template v-if="product.spec_name"> · {{ product.spec_name }}</template></small><div class="bar-track"><i class="orange" :style="{ width: barWidth(product.order_amount, maxProductAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatUnits(product.units) }} 件</strong><span>{{ formatMoney(product.order_amount) }}</span></div>
            </div>
            <div v-if="!result.products.length" class="mini-empty">暂无商品数据</div>
          </div>
        </article>
      </section>

      <section class="detail-panel panel">
        <div class="panel-heading detail-heading"><div><h3>订单明细预览</h3><p>当前筛选 {{ formatNumber(filteredDetailRows.length) }} 行，第 {{ formatNumber(detailPage) }} / {{ formatNumber(detailPageCount) }} 页；完整 {{ formatNumber(result.row_count) }} 行可通过 CSV 下载。</p></div><div class="detail-tools"><label class="search-box"><Search :size="16" /><input v-model="detailKeyword" type="search" placeholder="搜索订单号、店铺或商品" /></label><button class="secondary-button" type="button" @click="downloadCsv()"><Download :size="16" />下载 CSV</button></div></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th v-for="column in tableColumns" :key="column">{{ columnLabel(column) }}</th></tr></thead>
            <tbody>
              <tr v-for="(row, index) in filteredRows" :key="`${row['trade.trade_id'] || row['trade.trade_no']}-${index}`">
                <td v-for="column in tableColumns" :key="column" :title="formatCell(column, row[column])">{{ formatCell(column, row[column]) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="table-footer"><span>表格预览列 {{ tableColumns.length }} / CSV 字段 {{ result.columns.length }}</span><div class="table-pagination"><button class="table-page-button" type="button" :disabled="detailPage <= 1" aria-label="上一页" @click="detailPage -= 1"><ChevronLeft :size="15" /></button><span>{{ detailPage }} / {{ detailPageCount }}</span><button class="table-page-button" type="button" :disabled="detailPage >= detailPageCount" aria-label="下一页" @click="detailPage += 1"><ChevronRight :size="15" /></button></div><span>已自动合并同一订单的商品明细</span></div>
      </section>
    </template>

    <section v-else-if="result && !hasOrders" class="empty-state panel">
      <Table2 :size="40" /><strong>当前条件没有匹配订单</strong><span>可以调整日期或平台后重新查询。</span>
    </section>
    <section v-else class="empty-state panel initial-empty">
      <div class="empty-icon"><BarChart3 :size="28" /></div><strong>准备开始分析</strong><span>选择近三天范围后点击“查询订单”，这里会展示时间趋势、店铺排行、商品排行和订单明细。</span>
    </section>
  </main>
</template>
