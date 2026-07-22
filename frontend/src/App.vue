<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Database,
  Download,
  Filter,
  Loader2,
  Maximize2,
  Package,
  RefreshCw,
  Search,
  ShoppingCart,
  Store,
  Table2,
  TrendingUp,
  X,
} from '@lucide/vue'

// Use same-origin /api in both development and production.
// Vite and Nginx proxy /api to the FastAPI service respectively.
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''
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

const dateLayerOptions = ['今日', '昨日', '前天']

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

function recentDateWindow(now = new Date()) {
  const end = new Date(now)
  const start = new Date(end)
  start.setDate(start.getDate() - 2)
  return {
    startAt: `${toDateInput(start)}T00:00`,
    endAt: `${toDateInput(end)}T23:59`,
  }
}

function defaultFilters() {
  return {
    platformId: '39',
    timeType: 1,
    pageSize: 100,
    maxPages: null,
    exportAfterQuery: false,
  }
}

function defaultDashboardFilters() {
  return {
    brandKeyword: '',
    skuCodes: [],
    productNames: [],
    shopNames: [],
    ownerNames: [],
    dateLayers: [],
    timeTruncated: true,
  }
}

const filters = reactive(defaultFilters())
const dashboardFilters = reactive(defaultDashboardFilters())
const result = ref(null)
const loading = ref(false)
const csvDownloading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const detailKeyword = ref('')
const detailPage = ref(1)
const lastQueriedAt = ref('')
const lastSyncedAt = ref('')
const sseStatus = ref('connecting')
const expandedLineChartId = ref('')
const activeLinePointHour = ref(null)
const dashboardFiltersDirty = ref(false)
let orderEventSource = null
const PREVIEW_PAGE_SIZE = 120

function loadFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY) || 'null')
    if (saved && typeof saved === 'object') {
      const savedFilters = { ...saved }
      delete savedFilters.startAt
      delete savedFilters.endAt
      Object.assign(filters, savedFilters)
    }
  } catch {
    localStorage.removeItem(FILTERS_KEY)
  }
}

function saveFilters() {
  localStorage.setItem(FILTERS_KEY, JSON.stringify({ ...filters }))
}

function resetFilters() {
  Object.assign(filters, defaultFilters())
  Object.assign(dashboardFilters, defaultDashboardFilters())
  dashboardFiltersDirty.value = false
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

function buildDashboardRequestBody(dateWindow, includeRows = false) {
  return {
    start_time: toApiDateTime(dateWindow.startAt),
    end_time: toApiDateTime(dateWindow.endAt, true),
    platform_ids: filters.platformId === 'all' ? [] : [filters.platformId],
    page_size: Number(filters.pageSize),
    time_type: Number(filters.timeType),
    include_rows: includeRows,
    // 留空表示由后端依据 total_count 自动拉取完整分页。
    max_pages: filters.maxPages ? Number(filters.maxPages) : null,
    dashboard_filters: {
      brand: dashboardFilters.brandKeyword.trim() ? [dashboardFilters.brandKeyword.trim()] : [],
      sku_codes: dashboardFilters.skuCodes,
      product_names: dashboardFilters.productNames,
      shop_names: dashboardFilters.shopNames,
      owner_names: dashboardFilters.ownerNames,
      date_layers: dashboardFilters.dateLayers,
      time_truncated: dashboardFilters.timeTruncated,
    },
  }
}

async function queryOrders({ silent = false, includeRows = true } = {}) {
  if (loading.value) return null

  if (!silent) {
    errorMessage.value = ''
    successMessage.value = ''
    detailKeyword.value = ''
  }

  const dateWindow = recentDateWindow()
  saveFilters()
  loading.value = true
  try {
    const data = await fetchJson(`${API_BASE_URL}/api/wdt/orders/dashboard`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildDashboardRequestBody(dateWindow, includeRows)),
    })
    result.value = data
    lastQueriedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    lastSyncedAt.value = data.last_synced_at || ''
    if (!silent) {
      successMessage.value = data.order_count
        ? `已从本地 MySQL 加载 ${formatNumber(data.order_count)} 笔订单，覆盖 ${data.source_window_count} 个日窗口。`
        : '读取完成，当前时间范围没有匹配订单。'
      if (filters.exportAfterQuery && data.rows?.length) downloadCsv()
    }
    return data
  } catch (error) {
    if (!silent || !result.value) result.value = null
    errorMessage.value = error.message || '查询失败，请稍后重试。'
    return null
  } finally {
    loading.value = false
  }
}

function filterList(value) {
  return Array.isArray(value) ? value : []
}

function selectedFilterLabel(values, allLabel = '(全部)') {
  const selected = filterList(values)
  if (!selected.length) return allLabel
  if (selected.length === 1) return selected[0]
  return `${selected.length} 项已选`
}

function markDashboardFiltersDirty() {
  dashboardFiltersDirty.value = true
}

function applyDashboardFilters() {
  dashboardFiltersDirty.value = false
  queryOrders({ includeRows: false })
}

function clearDashboardFilters() {
  Object.assign(dashboardFilters, defaultDashboardFilters())
  applyDashboardFilters()
}

function selectDashboardDimension(dimension, value) {
  const filterKey = {
    shop: 'shopNames',
    product: 'productNames',
    owner: 'ownerNames',
  }[dimension]
  if (!filterKey || !value) return

  const selected = dashboardFilters[filterKey]
  dashboardFilters[filterKey] = selected.length === 1 && selected[0] === value ? [] : [value]
  dashboardFiltersDirty.value = false
  queryOrders({ includeRows: false })
}

function connectOrderEvents() {
  orderEventSource?.close()
  sseStatus.value = 'connecting'
  orderEventSource = new EventSource(`${API_BASE_URL}/api/wdt/orders/events`)

  orderEventSource.addEventListener('connected', () => {
    sseStatus.value = 'connected'
  })
  orderEventSource.addEventListener('orders.updated', async (event) => {
    const payload = JSON.parse(event.data || '{}')
    lastSyncedAt.value = payload.synced_at || lastSyncedAt.value
    await queryOrders({ silent: true, includeRows: false })
  })
  orderEventSource.addEventListener('orders.sync_failed', (event) => {
    sseStatus.value = 'error'
    const payload = JSON.parse(event.data || '{}')
    errorMessage.value = `后台同步失败：${payload.message || '请检查后端日志'}`
  })
  orderEventSource.onerror = () => {
    sseStatus.value = 'reconnecting'
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

function formatGrowth(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return `${number > 0 ? '+' : ''}${formatNumber(number, 1)}%`
}

function growthClass(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 'neutral'
  return number > 0 ? 'positive' : number < 0 ? 'negative' : 'neutral'
}

const hourlyRows = computed(() => result.value?.hourly || [])
const comparisonMeta = computed(() => result.value?.comparison || {})
const comparisonTodayLabel = computed(() => comparisonMeta.value.today_label || comparisonMeta.value.today || '当前日')
const comparisonYesterdayLabel = computed(() => comparisonMeta.value.yesterday_label || comparisonMeta.value.yesterday || '上一日')
const comparisonCutoffLabel = computed(() => {
  const cutoff = Number(comparisonMeta.value.cutoff_hour)
  if (!Number.isFinite(cutoff)) return '当前小时'
  return cutoff >= 24 ? '完整 24 小时' : `截至 ${cutoff}:00`
})

function formatLineValue(value, valueType) {
  return valueType === 'units' ? formatUnits(value) : formatNumber(value, 0)
}

function lineValueType(line) {
  return line?.id.includes('product') ? 'units' : 'amount'
}

function lineGrowth(today, yesterday) {
  const current = Number(today || 0)
  const previous = Number(yesterday || 0)
  if (!previous) return current ? '—' : '0.0%'
  return formatGrowth(((current - previous) / previous) * 100)
}

function buildHourlyLineChart(rows, todayField, yesterdayField, valueType) {
  const width = 760
  const height = 238
  const padX = 30
  const padTop = 16
  const padBottom = 28
  const max = Math.max(
    ...rows.flatMap((item) => [Number(item[todayField] || 0), Number(item[yesterdayField] || 0)]),
    1,
  )
  const plotHeight = height - padTop - padBottom
  const step = rows.length > 1 ? (width - padX * 2) / (rows.length - 1) : 0
  const points = rows.map((item, index) => {
    const today = Number(item[todayField] || 0)
    const yesterday = Number(item[yesterdayField] || 0)
    const x = rows.length > 1 ? padX + step * index : width / 2
    return {
      hour: item.hour,
      label: item.label,
      x,
      today,
      yesterday,
      todayY: padTop + plotHeight - (today / max) * plotHeight,
      yesterdayY: padTop + plotHeight - (yesterday / max) * plotHeight,
      showPoint: index % 3 === 0 || index === rows.length - 1,
    }
  })
  return {
    width,
    height,
    max,
    maxLabel: formatLineValue(max, valueType),
    midLabel: formatLineValue(max / 2, valueType),
    zeroLabel: formatLineValue(0, valueType),
    points,
    polylineToday: points.map((point) => `${point.x},${point.todayY}`).join(' '),
    polylineYesterday: points.map((point) => `${point.x},${point.yesterdayY}`).join(' '),
    xLabels: points.map((point) => ({
      hour: point.hour,
      label: point.hour % 3 === 0 || point.hour === points.at(-1)?.hour ? point.label.replace(':00', '') : '',
    })),
  }
}

const hourlyLineCharts = computed(() => [
  {
    id: 'sales-cumulative',
    title: '累计实收金额',
    subtitle: `24小时对比${comparisonYesterdayLabel.value}增长`,
    unit: '元',
    chart: buildHourlyLineChart(hourlyRows.value, 'today_cumulative_amount', 'yesterday_cumulative_amount', 'amount'),
  },
  {
    id: 'sales-hourly',
    title: '每小时实收金额',
    subtitle: `24小时对比${comparisonYesterdayLabel.value}波动`,
    unit: '元',
    chart: buildHourlyLineChart(hourlyRows.value, 'today_amount', 'yesterday_amount', 'amount'),
  },
  {
    id: 'product-cumulative',
    title: '累计商品数量',
    subtitle: `产品维度 · 24小时对比${comparisonYesterdayLabel.value}增长`,
    unit: '件',
    chart: buildHourlyLineChart(hourlyRows.value, 'today_cumulative_units', 'yesterday_cumulative_units', 'units'),
  },
  {
    id: 'product-hourly',
    title: '每小时商品数量',
    subtitle: `产品维度 · 24小时对比${comparisonYesterdayLabel.value}波动`,
    unit: '件',
    chart: buildHourlyLineChart(hourlyRows.value, 'today_units', 'yesterday_units', 'units'),
  },
])
const expandedLineChart = computed(() => hourlyLineCharts.value.find((line) => line.id === expandedLineChartId.value) || null)

function openLineChart(line) {
  expandedLineChartId.value = line.id
  activeLinePointHour.value = line.chart.points[0]?.hour ?? null
}

function closeExpandedLineChart() {
  expandedLineChartId.value = ''
  activeLinePointHour.value = null
}

function handleWindowKeydown(event) {
  if (event.key === 'Escape') closeExpandedLineChart()
}

function showLinePoint(point) {
  activeLinePointHour.value = point.hour
}

const activeLinePoint = computed(() => expandedLineChart.value?.chart.points.find((point) => point.hour === activeLinePointHour.value) || null)

function pointTooltipX(point) {
  return Math.min(Math.max(point.x - 78, 3), 597)
}

function pointTooltipY(point) {
  const pointTop = Math.min(point.todayY, point.yesterdayY)
  return Math.min(Math.max(pointTop - 70, 3), 157)
}

const shopComparisonRows = computed(() => (result.value?.shop_comparison || []).slice(0, 8))
const productComparisonRows = computed(() => (result.value?.product_comparison || []).slice(0, 8))
const ownerComparisonRows = computed(() => result.value?.owner_comparison || [])
const hiddenPddOwnerName = '淘宝 李世豪'
const isPddDashboard = computed(() => filters.platformId === '39')
const visibleOwnerFilterOptions = computed(() => filterList(filterOptions.value.owner_names).filter((owner) => !(isPddDashboard.value && owner === hiddenPddOwnerName)))
const visibleOwnerComparisonRows = computed(() => ownerComparisonRows.value
  .filter((owner) => !(isPddDashboard.value && owner.owner_name === hiddenPddOwnerName))
  .map((owner, index) => ({ ...owner, rank: index + 1 })))
const filterOptions = computed(() => result.value?.filter_options || {
  brands: [],
  sku_codes: [],
  product_names: [],
  shop_names: [],
  owner_names: [],
  brand_available: false,
})
const activeDashboardFilterCount = computed(() => [
  dashboardFilters.brandKeyword.trim(),
  ...dashboardFilters.skuCodes,
  ...dashboardFilters.productNames,
  ...dashboardFilters.shopNames,
  ...dashboardFilters.ownerNames,
  ...dashboardFilters.dateLayers,
  dashboardFilters.timeTruncated === false ? 'time_untruncated' : '',
].filter(Boolean).length)
const maxShopComparisonAmount = computed(() => Math.max(...shopComparisonRows.value.map((item) => Number(item.today_amount || 0)), 1))
const maxProductComparisonAmount = computed(() => Math.max(...productComparisonRows.value.map((item) => Number(item.today_amount || 0)), 1))
const maxOwnerComparisonAmount = computed(() => Math.max(...visibleOwnerComparisonRows.value.map((item) => Number(item.today_amount || 0)), 1))

function barWidth(value, max) {
  return `${Math.max(4, Math.min(100, (Number(value || 0) / max) * 100))}%`
}

function barHeight(value, max) {
  return `${Math.max(5, Math.min(100, (Number(value || 0) / max) * 100))}%`
}

function shopShare(amount) {
  return summary.value.order_amount ? `${formatNumber((Number(amount || 0) / summary.value.order_amount) * 100, 1)}%` : '0%'
}

async function downloadCsv() {
  if (csvDownloading.value) return
  if (loading.value) {
    errorMessage.value = '订单数据正在更新，请稍后再下载 CSV。'
    return
  }

  csvDownloading.value = true
  errorMessage.value = ''
  try {
    successMessage.value = '正在生成完整 CSV，请稍候…'
    const dateWindow = recentDateWindow()
    saveFilters()
    const response = await fetch(`${API_BASE_URL}/api/wdt/orders/dashboard/csv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(buildDashboardRequestBody(dateWindow)),
    })
    if (!response.ok) {
      const data = await response.json().catch(() => ({}))
      throw new Error(data.detail || `CSV 下载失败（${response.status}）`)
    }
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    const disposition = response.headers.get('Content-Disposition') || ''
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `wdt_orders_${dateWindow.startAt.slice(0, 10)}_${dateWindow.endAt.slice(0, 10)}.csv`
    link.download = filename
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    window.setTimeout(() => {
      link.remove()
      URL.revokeObjectURL(url)
    }, 1000)
    successMessage.value = 'CSV 已开始下载。'
  } catch (error) {
    errorMessage.value = error.message || 'CSV 下载失败，请稍后重试。'
  } finally {
    csvDownloading.value = false
  }
}

onMounted(async () => {
  loadFilters()
  connectOrderEvents()
  window.addEventListener('keydown', handleWindowKeydown)
  await queryOrders({ silent: true, includeRows: false })
})

onBeforeUnmount(() => {
  orderEventSource?.close()
  window.removeEventListener('keydown', handleWindowKeydown)
})
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
        <span>MySQL · SSE 实时更新</span>
        <span class="status-time">{{ sseStatus === 'connected' ? '已连接' : sseStatus === 'reconnecting' ? '正在重连' : '连接中' }}</span>
        <span v-if="lastQueriedAt" class="status-time">最近查询 {{ lastQueriedAt }}</span>
        <span v-if="lastSyncedAt" class="status-time">数据同步 {{ formatDateTime(lastSyncedAt) }}</span>
      </div>
    </header>

    <section class="query-panel">
      <div class="section-heading compact-heading">
        <div class="heading-icon"><Filter :size="18" /></div>
        <div>
          <h1>查询条件</h1>
          <p>数据由后端定时同步到 MySQL，当前看板固定查询前天、昨天和今天；SSE 会在同步完成后自动刷新。</p>
        </div>
      </div>

      <form class="query-form" @submit.prevent="queryOrders">
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
            {{ loading ? '正在读取本地数据…' : '读取订单' }}
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
        <button class="secondary-button" type="button" :disabled="csvDownloading" @click="downloadCsv()"><Loader2 v-if="csvDownloading" class="spin" :size="17" /><Download v-else :size="17" />{{ csvDownloading ? '正在生成 CSV…' : '下载完整 CSV' }}</button>
      </section>

      <section class="kpi-grid">
        <article class="kpi-card accent-blue"><div class="kpi-label"><ShoppingCart :size="17" />订单数</div><strong>{{ formatNumber(summary.order_count) }}</strong><span>明细行 {{ formatNumber(summary.detail_count) }}</span></article>
        <article class="kpi-card accent-green"><div class="kpi-label"><TrendingUp :size="17" />成交金额</div><strong>{{ formatMoney(summary.order_amount) }}</strong><span>客单价 {{ formatMoney(summary.avg_order_amount) }}</span></article>
        <article class="kpi-card accent-orange"><div class="kpi-label"><Package :size="17" />商品销量</div><strong>{{ formatUnits(summary.units) }}</strong><span>{{ formatNumber(summary.product_count) }} 个商品</span></article>
        <article class="kpi-card accent-purple"><div class="kpi-label"><Store :size="17" />店铺数</div><strong>{{ formatNumber(summary.shop_count) }}</strong><span>已按店铺聚合</span></article>
      </section>

      <section class="comparison-summary">
        <article class="comparison-card accent-green"><span>{{ comparisonTodayLabel }}实收金额</span><strong>{{ formatMoney(summary.today_amount) }}</strong><em :class="growthClass(summary.amount_growth_pct)">{{ formatGrowth(summary.amount_growth_pct) }} vs {{ comparisonYesterdayLabel }}</em></article>
        <article class="comparison-card accent-blue"><span>{{ comparisonTodayLabel }}订单数</span><strong>{{ formatNumber(summary.today_order_count) }}</strong><em :class="growthClass(summary.order_growth_pct)">{{ formatGrowth(summary.order_growth_pct) }} vs {{ comparisonYesterdayLabel }}</em></article>
        <article class="comparison-card accent-orange"><span>{{ comparisonTodayLabel }}商品数量</span><strong>{{ formatUnits(summary.today_units) }}</strong><em :class="growthClass(summary.units_growth_pct)">{{ formatGrowth(summary.units_growth_pct) }} vs {{ comparisonYesterdayLabel }}</em></article>
        <article class="comparison-card comparison-context"><span>对比口径</span><strong>{{ comparisonMeta.today || '-' }}</strong><em>{{ comparisonCutoffLabel }}，同小时对比</em></article>
      </section>

      <div class="filter-owner-row">
        <section class="tableau-filter-strip">
        <div class="filter-strip-heading">
          <div><span class="eyebrow">看板筛选器</span><h3>统一筛选条件</h3><p>一组筛选器统一联动销售额、产品维度、折线图、排名和订单明细。</p></div>
          <div class="filter-strip-actions"><span class="filter-count">已启用 {{ activeDashboardFilterCount }} 项</span><span v-if="dashboardFiltersDirty" class="filter-pending">待查询</span><button class="primary-button compact-button" type="button" :disabled="loading" @click="applyDashboardFilters"><Search :size="14" />按条件查询</button><button class="text-button" type="button" @click="clearDashboardFilters">清除看板筛选</button></div>
        </div>
          <div class="unified-filter-grid">
          <label class="filter-field"><span>品牌</span><input v-model="dashboardFilters.brandKeyword" type="search" placeholder="输入品牌" @change="markDashboardFiltersDirty" @keyup.enter="markDashboardFiltersDirty" /><small v-if="!filterOptions.brand_available">品牌字段待接入订单清洗</small></label>
          <label class="filter-field"><span>SKU 编码</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.skuCodes) }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.skuCodes.length" @change="dashboardFilters.skuCodes = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="sku in filterOptions.sku_codes" :key="sku" class="filter-option"><input v-model="dashboardFilters.skuCodes" type="checkbox" :value="sku" @change="markDashboardFiltersDirty" /> {{ sku }}</label></div></details></label>
          <label class="filter-field"><span>商品名称1</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.productNames) }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.productNames.length" @change="dashboardFilters.productNames = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="product in filterOptions.product_names" :key="product" class="filter-option"><input v-model="dashboardFilters.productNames" type="checkbox" :value="product" @change="markDashboardFiltersDirty" /> {{ product }}</label></div></details></label>
          <label class="filter-field"><span>店铺</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.shopNames) }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.shopNames.length" @change="dashboardFilters.shopNames = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="shop in filterOptions.shop_names" :key="shop" class="filter-option"><input v-model="dashboardFilters.shopNames" type="checkbox" :value="shop" @change="markDashboardFiltersDirty" /> {{ shop }}</label></div></details></label>
          <label class="filter-field"><span>负责人</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.ownerNames) }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.ownerNames.length" @change="dashboardFilters.ownerNames = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="owner in visibleOwnerFilterOptions" :key="owner" class="filter-option"><input v-model="dashboardFilters.ownerNames" type="checkbox" :value="owner" @change="markDashboardFiltersDirty" /> {{ owner }}</label></div></details></label>
          <label class="filter-field"><span>时间截断</span><select v-model="dashboardFilters.timeTruncated" @change="markDashboardFiltersDirty"><option :value="true">真</option><option :value="false">假</option></select></label>
          <label class="filter-field"><span>日期层级</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.dateLayers, '(多选)') }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.dateLayers.length" @change="dashboardFilters.dateLayers = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="layer in dateLayerOptions" :key="layer" class="filter-option"><input v-model="dashboardFilters.dateLayers" type="checkbox" :value="layer" @change="markDashboardFiltersDirty" /> {{ layer }}</label></div></details></label>
          </div>
        </section>

        <article class="panel ranking-panel owner-panel">
          <div class="panel-heading"><div><h3>负责人对比{{ comparisonYesterdayLabel }}</h3><p>按店铺负责人汇总{{ comparisonTodayLabel }}实收</p></div><Store :size="18" class="panel-icon" /></div>
          <div v-if="visibleOwnerComparisonRows.length" class="owner-bar-chart">
            <div v-for="owner in visibleOwnerComparisonRows" :key="owner.owner_name" :data-owner-name="owner.owner_name" :class="['owner-bar-item', 'is-clickable', { 'is-selected': dashboardFilters.ownerNames.includes(owner.owner_name) }]" role="button" tabindex="0" :title="`${owner.owner_name}：${formatMoney(owner.today_amount)}，${formatGrowth(owner.amount_growth_pct)}`" @click="selectDashboardDimension('owner', owner.owner_name)" @keydown.enter.prevent="selectDashboardDimension('owner', owner.owner_name)" @keydown.space.prevent="selectDashboardDimension('owner', owner.owner_name)">
              <strong class="owner-bar-value">{{ formatMoney(owner.today_amount) }}</strong>
              <div class="owner-bar-stage"><i :style="{ height: barHeight(owner.today_amount, maxOwnerComparisonAmount) }"></i></div>
              <b class="owner-bar-rank">{{ owner.rank }}</b>
              <strong class="owner-bar-name">{{ owner.owner_name }}</strong>
              <em :class="growthClass(owner.amount_growth_pct)">{{ formatGrowth(owner.amount_growth_pct) }}</em>
            </div>
          </div>
          <div v-else class="mini-empty">暂无已匹配负责人数据</div>
          <div class="mapping-note">负责人映射覆盖 {{ formatNumber(comparisonMeta.owner_mapping_coverage_pct, 1) }}% 订单</div>
        </article>
      </div>

      <section class="tableau-grid">
        <article class="panel hourly-panel tableau-line-panel">
          <div class="panel-heading"><div><h3>24 小时付款时间折线</h3><p>{{ comparisonMeta.today || '-' }} 与 {{ comparisonMeta.yesterday || '-' }} 的同小时趋势</p></div></div>
          <div class="hourly-legend"><span><i class="legend-swatch today"></i>{{ comparisonTodayLabel }}</span><span><i class="legend-swatch yesterday"></i>{{ comparisonYesterdayLabel }}</span><span class="legend-hint">付款时间 · {{ comparisonCutoffLabel }}</span></div>
          <div v-if="hourlyRows.length" class="hourly-line-grid">
            <article v-for="line in hourlyLineCharts" :key="line.id" class="hourly-line-card">
              <div class="line-card-heading"><div><strong>{{ line.title }}</strong><span>{{ line.subtitle }}</span></div><div class="line-card-actions"><em>{{ line.unit }}</em><button class="chart-zoom-button" type="button" :aria-label="`放大${line.title}`" :title="`放大${line.title}`" @click="openLineChart(line)"><Maximize2 :size="14" /></button></div></div>
              <div class="line-chart">
                <div class="line-y-axis"><span>{{ line.chart.maxLabel }}</span><span>{{ line.chart.midLabel }}</span><span>{{ line.chart.zeroLabel }}</span></div>
                <svg :viewBox="`0 0 ${line.chart.width} ${line.chart.height}`" preserveAspectRatio="none" role="img" :aria-label="line.title">
                  <line v-for="grid in [0, 1, 2]" :key="grid" x1="30" :y1="16 + grid * 97" x2="730" :y2="16 + grid * 97" class="line-grid" />
                  <polyline :points="line.chart.polylineYesterday" class="comparison-line yesterday" />
                  <polyline :points="line.chart.polylineToday" class="comparison-line today" />
                  <g v-for="point in line.chart.points" :key="point.hour">
                    <template v-if="point.showPoint">
                      <circle :cx="point.x" :cy="point.yesterdayY" r="2.6" class="line-point yesterday" />
                      <circle :cx="point.x" :cy="point.todayY" r="2.6" class="line-point today" />
                      <title>{{ point.label }}：{{ comparisonTodayLabel }} {{ formatLineValue(point.today, line.id.includes('product') ? 'units' : 'amount') }} / {{ comparisonYesterdayLabel }} {{ formatLineValue(point.yesterday, line.id.includes('product') ? 'units' : 'amount') }}</title>
                    </template>
                  </g>
                </svg>
                <div class="line-x-axis" :style="{ gridTemplateColumns: `repeat(${line.chart.xLabels.length}, minmax(0, 1fr))` }"><span v-for="tick in line.chart.xLabels" :key="tick.hour">{{ tick.label }}</span></div>
              </div>
            </article>
          </div>
          <div v-else class="mini-empty">暂无小时折线数据</div>
        </article>

      </section>

      <div v-if="expandedLineChart" class="chart-modal" role="dialog" aria-modal="true" :aria-label="`${expandedLineChart.title}放大看板`" @click.self="closeExpandedLineChart">
        <section class="chart-modal-dialog">
          <div class="chart-modal-heading"><div><span class="eyebrow">节点明细</span><h3>{{ expandedLineChart.title }}</h3><p>{{ expandedLineChart.subtitle }} · {{ expandedLineChart.unit }}</p></div><button class="modal-close-button" type="button" aria-label="关闭放大看板" title="关闭" @click="closeExpandedLineChart"><X :size="18" /></button></div>
          <div class="hourly-legend modal-legend"><span><i class="legend-swatch today"></i>{{ comparisonTodayLabel }}</span><span><i class="legend-swatch yesterday"></i>{{ comparisonYesterdayLabel }}</span><span class="legend-hint">点击右上角按钮或按 Esc 关闭</span></div>
          <div class="line-chart modal-line-chart">
            <div class="line-y-axis"><span>{{ expandedLineChart.chart.maxLabel }}</span><span>{{ expandedLineChart.chart.midLabel }}</span><span>{{ expandedLineChart.chart.zeroLabel }}</span></div>
            <svg :viewBox="`0 0 ${expandedLineChart.chart.width} ${expandedLineChart.chart.height}`" preserveAspectRatio="none" role="img" :aria-label="expandedLineChart.title">
              <line v-for="grid in [0, 1, 2]" :key="grid" x1="30" :y1="16 + grid * 97" x2="730" :y2="16 + grid * 97" class="line-grid" />
              <polyline :points="expandedLineChart.chart.polylineYesterday" class="comparison-line yesterday" />
              <polyline :points="expandedLineChart.chart.polylineToday" class="comparison-line today" />
              <g v-for="point in expandedLineChart.chart.points" :key="point.hour" tabindex="0" role="button" :aria-label="`${point.label} ${comparisonTodayLabel} ${formatLineValue(point.today, lineValueType(expandedLineChart))}，${comparisonYesterdayLabel} ${formatLineValue(point.yesterday, lineValueType(expandedLineChart))}，增长 ${lineGrowth(point.today, point.yesterday)}`" @mouseenter="showLinePoint(point)" @focus="showLinePoint(point)" @click="showLinePoint(point)">
                <circle :cx="point.x" :cy="point.yesterdayY" r="10" class="line-hit-area" @mouseenter="showLinePoint(point)" @click.stop="showLinePoint(point)" />
                <circle :cx="point.x" :cy="point.yesterdayY" r="3.5" :class="['line-point yesterday', { active: activeLinePointHour === point.hour }]" />
                <circle :cx="point.x" :cy="point.todayY" r="10" class="line-hit-area" @mouseenter="showLinePoint(point)" @click.stop="showLinePoint(point)" />
                <circle :cx="point.x" :cy="point.todayY" r="3.5" :class="['line-point today', { active: activeLinePointHour === point.hour }]" />
                <title>{{ point.label }}：今日 {{ formatLineValue(point.today, lineValueType(expandedLineChart)) }} / 昨日 {{ formatLineValue(point.yesterday, lineValueType(expandedLineChart)) }}</title>
              </g>
              <g v-if="activeLinePoint" class="line-point-tooltip" :transform="`translate(${pointTooltipX(activeLinePoint)}, ${pointTooltipY(activeLinePoint)})`">
                <rect width="168" height="78" rx="5" />
                <text x="8" y="15" class="tooltip-title">{{ activeLinePoint.label }}</text>
                <text x="8" y="31">{{ comparisonTodayLabel }}：{{ formatLineValue(activeLinePoint.today, lineValueType(expandedLineChart)) }}{{ expandedLineChart.unit }}</text>
                <text x="8" y="47">{{ comparisonYesterdayLabel }}：{{ formatLineValue(activeLinePoint.yesterday, lineValueType(expandedLineChart)) }}{{ expandedLineChart.unit }}</text>
                <text x="8" y="63">差值：{{ formatLineValue(activeLinePoint.today - activeLinePoint.yesterday, lineValueType(expandedLineChart)) }}{{ expandedLineChart.unit }}</text>
                <text x="8" y="74" :class="growthClass(activeLinePoint.today - activeLinePoint.yesterday)">增长：{{ lineGrowth(activeLinePoint.today, activeLinePoint.yesterday) }}</text>
              </g>
            </svg>
            <div class="line-x-axis" :style="{ gridTemplateColumns: `repeat(${expandedLineChart.chart.xLabels.length}, minmax(0, 1fr))` }"><span v-for="tick in expandedLineChart.chart.xLabels" :key="tick.hour">{{ tick.label }}</span></div>
          </div>
          <div class="chart-node-table-wrap">
            <table class="chart-node-table">
              <thead><tr><th>时间节点</th><th>{{ comparisonTodayLabel }}{{ expandedLineChart.unit }}</th><th>{{ comparisonYesterdayLabel }}{{ expandedLineChart.unit }}</th><th>差值</th><th>增长</th></tr></thead>
              <tbody><tr v-for="point in expandedLineChart.chart.points" :key="`node-${point.hour}`"><td>{{ point.label }}</td><td>{{ formatLineValue(point.today, lineValueType(expandedLineChart)) }}</td><td>{{ formatLineValue(point.yesterday, lineValueType(expandedLineChart)) }}</td><td>{{ formatLineValue(point.today - point.yesterday, lineValueType(expandedLineChart)) }}</td><td :class="growthClass(point.today - point.yesterday)">{{ lineGrowth(point.today, point.yesterday) }}</td></tr></tbody>
            </table>
          </div>
        </section>
      </div>

      <section class="comparison-ranking-grid">
        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>店铺对比{{ comparisonYesterdayLabel }}排名</h3><p>按{{ comparisonTodayLabel }}实收金额排序</p></div><Store :size="18" class="panel-icon" /></div>
          <div class="ranking-list comparison-ranking-list">
            <div v-for="shop in shopComparisonRows" :key="`${shop.shop_id}-${shop.shop_name}`" :data-shop-name="shop.shop_name" :class="['ranking-row', 'comparison-ranking-row', 'is-clickable', { 'is-selected': dashboardFilters.shopNames.includes(shop.shop_name) }]" role="button" tabindex="0" @click="selectDashboardDimension('shop', shop.shop_name)" @keydown.enter.prevent="selectDashboardDimension('shop', shop.shop_name)" @keydown.space.prevent="selectDashboardDimension('shop', shop.shop_name)">
              <b>{{ shop.rank }}</b><div class="ranking-main"><strong :title="shop.shop_name">{{ shop.shop_name }}</strong><div class="bar-track"><i :style="{ width: barWidth(shop.today_amount, maxShopComparisonAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatMoney(shop.today_amount) }}</strong><span><em :class="growthClass(shop.amount_growth_pct)">{{ formatGrowth(shop.amount_growth_pct) }}</em></span></div>
            </div>
          </div>
        </article>
        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>商品对比{{ comparisonYesterdayLabel }}排名</h3><p>按商品规格{{ comparisonTodayLabel }}实收金额排序</p></div><Package :size="18" class="panel-icon" /></div>
          <div class="ranking-list comparison-ranking-list">
            <div v-for="product in productComparisonRows" :key="`${product.product_no}-${product.spec_name}`" :data-product-name="product.product_name" :class="['ranking-row', 'comparison-ranking-row', 'is-clickable', { 'is-selected': dashboardFilters.productNames.includes(product.product_name) }]" role="button" tabindex="0" @click="selectDashboardDimension('product', product.product_name)" @keydown.enter.prevent="selectDashboardDimension('product', product.product_name)" @keydown.space.prevent="selectDashboardDimension('product', product.product_name)">
              <b>{{ product.rank }}</b><div class="ranking-main"><strong :title="product.product_name">{{ product.product_name }}</strong><small>{{ product.product_no || '无货号' }}<template v-if="product.spec_name"> · {{ product.spec_name }}</template></small><div class="bar-track"><i class="orange" :style="{ width: barWidth(product.today_amount, maxProductComparisonAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatMoney(product.today_amount) }}</strong><span>{{ formatUnits(product.today_units) }} 件 · <em :class="growthClass(product.amount_growth_pct)">{{ formatGrowth(product.amount_growth_pct) }}</em></span></div>
            </div>
          </div>
        </article>
      </section>

      <section class="analysis-grid ranking-overview-grid">
        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>店铺排行</h3><p>按成交金额排序</p></div><Store :size="18" class="panel-icon" /></div>
          <div class="ranking-list">
            <div v-for="(shop, index) in result.shops.slice(0, 8)" :key="`${shop.shop_id}-${shop.shop_name}`" :data-shop-name="shop.shop_name" :class="['ranking-row', 'is-clickable', { 'is-selected': dashboardFilters.shopNames.includes(shop.shop_name) }]" role="button" tabindex="0" @click="selectDashboardDimension('shop', shop.shop_name)" @keydown.enter.prevent="selectDashboardDimension('shop', shop.shop_name)" @keydown.space.prevent="selectDashboardDimension('shop', shop.shop_name)">
              <b>{{ index + 1 }}</b><div class="ranking-main"><strong :title="shop.shop_name">{{ shop.shop_name }}</strong><div class="bar-track"><i :style="{ width: barWidth(shop.order_amount, maxShopAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatMoney(shop.order_amount) }}</strong><span>{{ formatNumber(shop.order_count) }} 单 · {{ shopShare(shop.order_amount) }}</span></div>
            </div>
            <div v-if="!result.shops.length" class="mini-empty">暂无店铺数据</div>
          </div>
        </article>

        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>商品排行</h3><p>按商品行金额排序</p></div><Package :size="18" class="panel-icon" /></div>
          <div class="ranking-list">
            <div v-for="(product, index) in result.products.slice(0, 8)" :key="`${product.product_no}-${product.spec_name}`" :data-product-name="product.product_name" :class="['ranking-row', 'is-clickable', { 'is-selected': dashboardFilters.productNames.includes(product.product_name) }]" role="button" tabindex="0" @click="selectDashboardDimension('product', product.product_name)" @keydown.enter.prevent="selectDashboardDimension('product', product.product_name)" @keydown.space.prevent="selectDashboardDimension('product', product.product_name)">
              <b>{{ index + 1 }}</b><div class="ranking-main"><strong :title="product.product_name">{{ product.product_name }}</strong><small>{{ product.product_no || '无货号' }}<template v-if="product.spec_name"> · {{ product.spec_name }}</template></small><div class="bar-track"><i class="orange" :style="{ width: barWidth(product.order_amount, maxProductAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatUnits(product.units) }} 件</strong><span>{{ formatMoney(product.order_amount) }}</span></div>
            </div>
            <div v-if="!result.products.length" class="mini-empty">暂无商品数据</div>
          </div>
        </article>
      </section>

      <section class="detail-panel panel">
        <div class="panel-heading detail-heading"><div><h3>订单明细预览</h3><p v-if="result.rows_complete !== false">当前筛选 {{ formatNumber(filteredDetailRows.length) }} 行，第 {{ formatNumber(detailPage) }} / {{ formatNumber(detailPageCount) }} 页；完整 {{ formatNumber(result.row_count) }} 行可通过 CSV 下载。</p><p v-else>当前看板已使用快速预览，显示 {{ formatNumber(filteredDetailRows.length) }} 行；共 {{ formatNumber(result.row_count) }} 行，点击“下载 CSV”会加载完整明细。</p></div><div class="detail-tools"><label class="search-box"><Search :size="16" /><input v-model="detailKeyword" type="search" placeholder="搜索订单号、店铺或商品" /></label><button class="secondary-button" type="button" :disabled="csvDownloading" @click="downloadCsv()"><Loader2 v-if="csvDownloading" class="spin" :size="16" /><Download v-else :size="16" />{{ csvDownloading ? '正在生成 CSV…' : '下载 CSV' }}</button></div></div>
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
      <div class="empty-icon"><BarChart3 :size="28" /></div><strong>准备开始分析</strong><span>选择近三天范围后点击“查询订单”，这里会展示 24 小时折线、店铺排行、商品排行和订单明细。</span>
    </section>
  </main>
</template>
