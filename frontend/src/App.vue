<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  BarChart3,
  CheckCircle2,
  CircleAlert,
  Database,
  Download,
  Loader2,
  Maximize2,
  Package,
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
// 保留查询口径设置，兼容升级前已保存的平台与时间字段选择。
const FILTERS_KEY = 'wdt_order_analysis_filters_v2'

const platformOptions = [
  { id: 'all', name: '全部平台' },
  { id: '39', name: '拼多多' },
  { id: '1', name: '淘宝 / 天猫' },
  { id: '3', name: '京东' },
  { id: '127', name: '抖音' },
]

const brandOptions = [
  { value: '', label: '全部' },
  { value: '浪奇', label: '浪奇' },
  { value: '威王', label: '威王' },
  { value: '舒蕾', label: '舒蕾' },
  { value: '白牌', label: '白牌' },
]

const timeOptions = [
  { value: 4, label: '付款时间 pay_time' },
  { value: 5, label: '发货时间 consign_time' },
]

const dateLayerOptions = ['今日', '昨日', '前天']

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

function defaultDashboardFilters() {
  return {
    platformId: '39',
    timeType: 4,
    brandKeyword: '',
    skuCodes: [],
    productNames: [],
    shopNames: [],
    ownerNames: [],
    dateLayers: ['昨日', '今日'],
    hours: [],
    timeTruncated: true,
  }
}

const dashboardFilters = reactive(defaultDashboardFilters())
const activeDashboardTab = ref('sales')
const result = ref(null)
const loading = ref(false)
const csvDownloading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const lastQueriedAt = ref('')
const lastSyncedAt = ref('')
const sseStatus = ref('connecting')
const expandedLineChartId = ref('')
const activeLinePointHour = ref(null)
const tableauHover = ref(null)
const dashboardFiltersDirty = ref(false)
let orderEventSource = null

function loadFilters() {
  try {
    const saved = JSON.parse(localStorage.getItem(FILTERS_KEY) || 'null')
    if (saved && typeof saved === 'object') {
      if (saved.platformId) dashboardFilters.platformId = saved.platformId
      if ([4, 5].includes(Number(saved.timeType))) dashboardFilters.timeType = Number(saved.timeType)
    }
  } catch {
    localStorage.removeItem(FILTERS_KEY)
  }
}

function saveFilters() {
  localStorage.setItem(FILTERS_KEY, JSON.stringify({
    platformId: dashboardFilters.platformId,
    timeType: dashboardFilters.timeType,
  }))
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
    platform_ids: dashboardFilters.platformId === 'all' ? [] : [dashboardFilters.platformId],
    page_size: 100,
    time_type: Number(dashboardFilters.timeType),
    include_rows: includeRows,
    // 留空表示由后端依据 total_count 自动拉取完整分页。
    max_pages: null,
    dashboard_filters: {
      brand: dashboardFilters.brandKeyword.trim() ? [dashboardFilters.brandKeyword.trim()] : [],
      sku_codes: dashboardFilters.skuCodes,
      product_names: dashboardFilters.productNames,
      shop_names: dashboardFilters.shopNames,
      owner_names: dashboardFilters.ownerNames,
      date_layers: dashboardFilters.dateLayers,
      hours: dashboardFilters.hours,
      time_truncated: dashboardFilters.timeTruncated,
    },
  }
}

async function queryOrders({ silent = false, includeRows = false } = {}) {
  if (loading.value) return null

  if (!silent) {
    errorMessage.value = ''
    successMessage.value = ''
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
  if (selected.length === dateLayerOptions.length && selected.every((value) => dateLayerOptions.includes(value))) return '全部'
  if (selected.length === 1) return selected[0]
  if (selected.length <= 3) return selected.join('、')
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

function selectDashboardHour(hour) {
  const nextHour = Number(hour)
  if (!Number.isFinite(nextHour)) return
  dashboardFilters.hours = dashboardFilters.hours.length === 1 && dashboardFilters.hours[0] === nextHour ? [] : [nextHour]
  dashboardFiltersDirty.value = false
  queryOrders({ includeRows: false })
}

function showTableauPoint(chart, series, point, valueType) {
  tableauHover.value = {
    chartId: chart.id,
    seriesLabel: series.label || series.date,
    point,
    valueType,
  }
}

function clearTableauPoint() {
  tableauHover.value = null
}

function selectTableauPoint(chart, series, point, valueType) {
  const layer = dateLayerOptions.includes(series.label) ? series.label : ''
  if (layer) dashboardFilters.dateLayers = [layer]
  dashboardFilters.hours = [Number(point.hour)]
  dashboardFiltersDirty.value = false
  showTableauPoint(chart, series, point, valueType)
  queryOrders({ includeRows: false })
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

const summary = computed(() => result.value?.summary || {})
const hasOrders = computed(() => Number(summary.value.order_count || 0) > 0)
const maxShopAmount = computed(() => Math.max(...(result.value?.shops || []).map((item) => Number(item.order_amount || 0)), 1))
const maxProductAmount = computed(() => Math.max(...(result.value?.products || []).map((item) => Number(item.order_amount || 0)), 1))

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
const activeTimeFieldLabel = computed(() => timeOptions.find((option) => option.value === Number(dashboardFilters.timeType))?.label.split(' ')[0] || '付款时间')
const comparisonTodayLabel = computed(() => comparisonMeta.value.today_label || comparisonMeta.value.today || '当前日')
const comparisonYesterdayLabel = computed(() => comparisonMeta.value.yesterday_label || comparisonMeta.value.yesterday || '上一日')
const hourlySeries = computed(() => {
  if (result.value?.hourly_series?.length) return result.value.hourly_series
  if (!hourlyRows.value.length) return []
  return [
    {
      date: comparisonMeta.value.today,
      label: comparisonTodayLabel.value,
      hours: hourlyRows.value.map((item) => ({
        hour: item.hour,
        label: item.label,
        amount: item.today_amount,
        units: item.today_units,
        cumulative_amount: item.today_cumulative_amount,
        cumulative_units: item.today_cumulative_units,
      })),
    },
    {
      date: comparisonMeta.value.yesterday,
      label: comparisonYesterdayLabel.value,
      hours: hourlyRows.value.map((item) => ({
        hour: item.hour,
        label: item.label,
        amount: item.yesterday_amount,
        units: item.yesterday_units,
        cumulative_amount: item.yesterday_cumulative_amount,
        cumulative_units: item.yesterday_cumulative_units,
      })),
    },
  ]
})
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

function buildHourlySeriesChart(seriesRows, valueField, valueType) {
  const width = 760
  const height = 238
  const padX = 30
  const padTop = 16
  const padBottom = 28
  const palette = ['#4e79a7', '#edc949', '#8b949e', '#8b5cf6', '#10b981']
  const dashPatterns = ['', '6 4', '2 4', '9 4 2 4', '3 3']
  const normalizedSeries = (Array.isArray(seriesRows) ? seriesRows : []).map((series, seriesIndex) => ({
    key: series.date || `series-${seriesIndex}`,
    label: series.label || series.date || `第 ${seriesIndex + 1} 天`,
    color: palette[seriesIndex % palette.length],
    dash: dashPatterns[seriesIndex % dashPatterns.length],
    rows: Array.isArray(series.hours) ? series.hours : [],
  }))
  const hours = [...new Set(normalizedSeries.flatMap((series) => series.rows.map((item) => Number(item.hour))))]
    .filter((hour) => Number.isFinite(hour))
    .sort((left, right) => left - right)
  const max = Math.max(
    ...normalizedSeries.flatMap((series) => series.rows.map((item) => Number(item[valueField] || 0))),
    1,
  )
  const plotHeight = height - padTop - padBottom
  const step = hours.length > 1 ? (width - padX * 2) / (hours.length - 1) : 0
  const pointsForSeries = (series) => hours.map((hour, index) => {
    const item = series.rows.find((row) => Number(row.hour) === hour) || { hour, label: `${hour}:00` }
    const value = Number(item[valueField] || 0)
    const x = hours.length > 1 ? padX + step * index : width / 2
    return {
      hour,
      label: item.label,
      x,
      value,
      y: padTop + plotHeight - (value / max) * plotHeight,
      showPoint: index % 3 === 0 || index === hours.length - 1,
    }
  })
  const series = normalizedSeries.map((item) => ({ ...item, points: pointsForSeries(item) }))
  const primaryPoints = series[0]?.points || []
  const secondaryPoints = series[1]?.points || []
  const points = primaryPoints.map((point, index) => ({
    ...point,
    today: point.value,
    yesterday: secondaryPoints[index]?.value || 0,
    todayY: point.y,
    yesterdayY: secondaryPoints[index]?.y || padTop + plotHeight,
  }))
  return {
    width,
    height,
    max,
    maxLabel: formatLineValue(max, valueType),
    midLabel: formatLineValue(max / 2, valueType),
    zeroLabel: formatLineValue(0, valueType),
    series,
    points,
    xLabels: primaryPoints.map((point) => ({
      hour: point.hour,
      label: point.hour % 3 === 0 || point.hour === primaryPoints.at(-1)?.hour ? point.label.replace(':00', '') : '',
    })),
  }
}

const hourlyLineCharts = computed(() => [
  {
    id: 'sales-cumulative',
    title: '累计实收金额',
    subtitle: `24小时对比${comparisonYesterdayLabel.value}增长`,
    unit: '元',
    chart: buildHourlySeriesChart(hourlySeries.value, 'cumulative_amount', 'amount'),
  },
  {
    id: 'sales-hourly',
    title: '每小时实收金额',
    subtitle: `24小时对比${comparisonYesterdayLabel.value}波动`,
    unit: '元',
    chart: buildHourlySeriesChart(hourlySeries.value, 'amount', 'amount'),
  },
  {
    id: 'product-cumulative',
    title: '累计商品数量',
    subtitle: `产品维度 · 24小时对比${comparisonYesterdayLabel.value}增长`,
    unit: '件',
    chart: buildHourlySeriesChart(hourlySeries.value, 'cumulative_units', 'units'),
  },
  {
    id: 'product-hourly',
    title: '每小时商品数量',
    subtitle: `产品维度 · 24小时对比${comparisonYesterdayLabel.value}波动`,
    unit: '件',
    chart: buildHourlySeriesChart(hourlySeries.value, 'units', 'units'),
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

const shopComparisonRows = computed(() => result.value?.shop_comparison || [])
const productComparisonRows = computed(() => result.value?.product_comparison || [])
const ownerComparisonRows = computed(() => result.value?.owner_comparison || [])
const hiddenPddOwnerName = '淘宝 李世豪'
const isPddDashboard = computed(() => dashboardFilters.platformId === '39')
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
  dashboardFilters.platformId !== '39' ? dashboardFilters.platformId : '',
  dashboardFilters.timeType !== 4 ? 'time_type' : '',
  dashboardFilters.brandKeyword.trim(),
  ...dashboardFilters.skuCodes,
  ...dashboardFilters.productNames,
  ...dashboardFilters.shopNames,
  ...dashboardFilters.ownerNames,
  ...dashboardFilters.dateLayers,
  ...dashboardFilters.hours.map((hour) => `hour-${hour}`),
  dashboardFilters.timeTruncated === false ? 'time_untruncated' : '',
].filter(Boolean).length)
const maxShopComparisonAmount = computed(() => Math.max(...shopComparisonRows.value.map((item) => Number(item.today_amount || 0)), 1))
const maxProductComparisonAmount = computed(() => Math.max(...productComparisonRows.value.map((item) => Number(item.today_amount || 0)), 1))
const maxOwnerComparisonAmount = computed(() => Math.max(...visibleOwnerComparisonRows.value.map((item) => Number(item.today_amount || 0)), 1))

function shortDate(value) {
  if (!value) return '-'
  const match = String(value).match(/(\d{4})-(\d{1,2})-(\d{1,2})/)
  return match ? `${Number(match[2])}.${String(match[3]).padStart(2, '0')}` : String(value)
}

function seriesIndexColor(series) {
  if (series?.key === comparisonMeta.value.today || series?.label === '今日') return '#4e79a7'
  if (series?.key === comparisonMeta.value.yesterday || series?.label === '昨日') return '#edc949'
  return '#8b949e'
}

const tableauDateTotal = computed(() => ({
  today: shortDate(comparisonMeta.value.today),
  yesterday: shortDate(comparisonMeta.value.yesterday),
  todayAmount: Number(summary.value.today_amount || 0),
  yesterdayAmount: Number(summary.value.yesterday_amount || 0),
  growth: summary.value.amount_growth_pct,
}))

const tableauOwnerRows = computed(() => visibleOwnerComparisonRows.value)
const tableauShopRows = computed(() => shopComparisonRows.value)
const tableauProductRows = computed(() => [...productComparisonRows.value]
  .sort((left, right) => Number(right.today_units || 0) - Number(left.today_units || 0))
  .map((item, index) => ({ ...item, tableauRank: index + 1 })))
const tableauSalesProductRows = computed(() => [...productComparisonRows.value]
  .sort((left, right) => Number(right.today_amount || 0) - Number(left.today_amount || 0))
  .map((item, index) => ({ ...item, tableauRank: index + 1 })))
const tableauPrimaryChart = computed(() => hourlyLineCharts.value.find((line) => line.id === (activeDashboardTab.value === 'sales' ? 'sales-hourly' : 'product-hourly')))
const tableauSecondaryChart = computed(() => hourlyLineCharts.value.find((line) => line.id === (activeDashboardTab.value === 'sales' ? 'sales-cumulative' : 'product-cumulative')))
const tableauFilterCount = computed(() => activeDashboardFilterCount.value)

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

    <div v-if="errorMessage" class="message error"><CircleAlert :size="18" /><span>{{ errorMessage }}</span></div>
    <div v-if="successMessage" class="message success"><CheckCircle2 :size="18" /><span>{{ successMessage }}</span></div>

    <template v-if="result && hasOrders">
      <section class="tableau-workspace">
        <div class="tableau-view-tabs" role="tablist" aria-label="Tableau 看板页签">
          <button type="button" role="tab" :aria-selected="activeDashboardTab === 'sales'" :class="{ active: activeDashboardTab === 'sales' }" @click="activeDashboardTab = 'sales'">销售额看板</button>
          <button type="button" role="tab" :aria-selected="activeDashboardTab === 'product'" :class="{ active: activeDashboardTab === 'product' }" @click="activeDashboardTab = 'product'">产品维度看板</button>
        </div>

        <section class="tableau-filter-bar">
          <label class="tableau-filter-field"><span>平台</span><select v-model="dashboardFilters.platformId" @change="markDashboardFiltersDirty"><option v-for="option in platformOptions" :key="option.id" :value="option.id">{{ option.name }}</option></select></label>
          <label class="tableau-filter-field"><span>时间字段</span><select v-model="dashboardFilters.timeType" @change="markDashboardFiltersDirty"><option v-for="option in timeOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
          <label class="tableau-filter-field"><span>品牌</span><select v-model="dashboardFilters.brandKeyword" @change="markDashboardFiltersDirty"><option v-for="option in brandOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
          <label v-if="activeDashboardTab === 'product'" class="tableau-filter-field"><span>SKU编码</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.skuCodes) }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.skuCodes.length" @change="dashboardFilters.skuCodes = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="sku in filterOptions.sku_codes" :key="sku" class="filter-option"><input v-model="dashboardFilters.skuCodes" type="checkbox" :value="sku" @change="markDashboardFiltersDirty" /> {{ sku }}</label></div></details></label>
          <label v-if="activeDashboardTab === 'product'" class="tableau-filter-field"><span>商品名称1</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.productNames) }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.productNames.length" @change="dashboardFilters.productNames = []; markDashboardFiltersDirty()" /> (全部)</label><label v-for="product in filterOptions.product_names" :key="product" class="filter-option"><input v-model="dashboardFilters.productNames" type="checkbox" :value="product" @change="markDashboardFiltersDirty" /> {{ product }}</label></div></details></label>
          <label class="tableau-filter-field"><span>时间截断</span><select v-model="dashboardFilters.timeTruncated" @change="markDashboardFiltersDirty"><option :value="true">真</option><option :value="false">假</option></select></label>
          <label class="tableau-filter-field"><span>日期层级</span><details class="filter-dropdown"><summary>{{ selectedFilterLabel(dashboardFilters.dateLayers, '全部') }} <b>⌄</b></summary><div class="filter-menu"><label class="filter-option"><input type="checkbox" :checked="!dashboardFilters.dateLayers.length" @change="dashboardFilters.dateLayers = []; markDashboardFiltersDirty()" /><span>全部</span></label><label v-for="layer in dateLayerOptions" :key="layer" class="filter-option"><input v-model="dashboardFilters.dateLayers" type="checkbox" :value="layer" @change="markDashboardFiltersDirty" /><span>{{ layer }}</span></label></div></details></label>
          <div class="tableau-filter-actions"><span v-if="tableauFilterCount">已启用 {{ tableauFilterCount }} 项</span><button type="button" class="tableau-query-button" :disabled="loading" @click="applyDashboardFilters"><Loader2 v-if="loading" class="spin" :size="13" /><Search v-else :size="13" />查询</button><button type="button" class="tableau-reset-button" @click="clearDashboardFilters">重置</button></div>
        </section>

        <section v-if="activeDashboardTab === 'sales'" class="tableau-canvas sales-canvas">
          <div class="tableau-column tableau-column-left">
            <article class="tableau-sheet date-total-sheet">
              <h2>日期总计</h2>
              <table class="tableau-data-table">
                <thead><tr><th>指标</th><th>{{ tableauDateTotal.yesterday }}</th><th>{{ tableauDateTotal.today }}</th><th>差异%</th></tr></thead>
                <tbody><tr><td>实收金额</td><td>{{ formatMoney(tableauDateTotal.yesterdayAmount) }}</td><td>{{ formatMoney(tableauDateTotal.todayAmount) }}</td><td :class="growthClass(tableauDateTotal.growth)">{{ formatGrowth(tableauDateTotal.growth) }}</td></tr></tbody>
              </table>
            </article>

            <article class="tableau-sheet">
              <div class="tableau-sheet-heading"><h2>店铺对比昨日排名</h2><span>按今日实收金额排序</span></div>
              <table class="tableau-data-table">
                <thead><tr><th>店铺</th><th>{{ tableauDateTotal.yesterday }}</th><th>{{ tableauDateTotal.today }}</th><th>差异%</th></tr></thead>
                <tbody><tr v-for="shop in tableauShopRows" :key="`${shop.shop_id}-${shop.shop_name}`" @click="selectDashboardDimension('shop', shop.shop_name)"><td>{{ shop.shop_name }}</td><td>{{ formatMoney(shop.yesterday_amount) }}</td><td>{{ formatMoney(shop.today_amount) }}</td><td :class="growthClass(shop.amount_growth_pct)">{{ formatGrowth(shop.amount_growth_pct) }}</td></tr></tbody>
              </table>
            </article>

            <article class="tableau-sheet">
              <div class="tableau-sheet-heading"><h2>负责人对比昨日排名</h2><span>按今日实收金额排序</span></div>
              <table class="tableau-data-table">
                <thead><tr><th>负责人</th><th>{{ tableauDateTotal.yesterday }}</th><th>{{ tableauDateTotal.today }}</th><th>差异%</th></tr></thead>
                <tbody><tr v-for="owner in tableauOwnerRows" :key="owner.owner_name" @click="selectDashboardDimension('owner', owner.owner_name)"><td>{{ owner.owner_name }}</td><td>{{ formatMoney(owner.yesterday_amount) }}</td><td>{{ formatMoney(owner.today_amount) }}</td><td :class="growthClass(owner.amount_growth_pct)">{{ formatGrowth(owner.amount_growth_pct) }}</td></tr></tbody>
              </table>
            </article>

            <article class="tableau-sheet">
              <div class="tableau-sheet-heading"><h2>商品对比昨日排名</h2><span>按商品编码今日实收金额排序</span></div>
              <table class="tableau-data-table">
                <thead><tr><th>商品名称1 / Sku编码</th><th>{{ tableauDateTotal.yesterday }}</th><th>{{ tableauDateTotal.today }}</th><th>差异%</th></tr></thead>
                <tbody><tr v-for="product in tableauSalesProductRows" :key="product.product_no || product.product_name" @click="selectDashboardDimension('product', product.product_name)"><td><strong>{{ product.product_name }}</strong><small>{{ product.product_no || '无货号' }}<template v-if="product.spec_name"> · {{ product.spec_name }}</template></small></td><td>{{ formatMoney(product.yesterday_amount) }}</td><td>{{ formatMoney(product.today_amount) }}</td><td :class="growthClass(product.amount_growth_pct)">{{ formatGrowth(product.amount_growth_pct) }}</td></tr></tbody>
              </table>
            </article>
          </div>

          <div class="tableau-column tableau-column-right">
            <article class="tableau-chart-sheet">
              <div class="tableau-sheet-heading"><h2>24小时对比昨日波动</h2><span>付款时间 · {{ comparisonCutoffLabel }}</span></div>
              <div v-if="tableauPrimaryChart" class="tableau-chart-frame">
                <div class="tableau-axis-labels"><span>{{ tableauPrimaryChart.chart.maxLabel }}</span><span>{{ tableauPrimaryChart.chart.midLabel }}</span><span>0</span></div>
                <svg :viewBox="`0 0 ${tableauPrimaryChart.chart.width} 270`" preserveAspectRatio="none" role="img" aria-label="24小时对比昨日波动">
                  <line v-for="grid in [0, 1, 2, 3]" :key="grid" x1="30" :y1="22 + grid * 72" x2="730" :y2="22 + grid * 72" class="tableau-grid-line" />
                  <template v-for="series in tableauPrimaryChart.chart.series" :key="`sales-primary-${series.key}`"><polyline :points="series.points.map((point) => `${point.x},${point.y + 8}`).join(' ')" class="tableau-comparison-line" :style="{ stroke: seriesIndexColor(series) }" /><template v-for="point in series.points" :key="`${series.key}-${point.hour}`"><circle :cx="point.x" :cy="point.y + 8" r="3" class="tableau-point-hit" :fill="seriesIndexColor(series)" tabindex="0" role="button" @mouseenter="showTableauPoint(tableauPrimaryChart, series, point, 'amount')" @mouseleave="clearTableauPoint" @focus="showTableauPoint(tableauPrimaryChart, series, point, 'amount')" @pointerdown="selectTableauPoint(tableauPrimaryChart, series, point, 'amount')" /><text :x="point.x" :y="Math.max(14, point.y - 1)" class="tableau-point-label" text-anchor="middle">{{ formatLineValue(point.value, 'amount') }}</text></template></template>
                </svg>
                <div class="tableau-x-axis"><span v-for="tick in tableauPrimaryChart.chart.xLabels" :key="tick.hour">{{ tick.label }}</span></div>
                <div v-if="tableauHover && tableauHover.chartId === tableauPrimaryChart.id" class="tableau-hover-card"><strong>{{ tableauHover.seriesLabel }}</strong><span>{{ tableauHover.point.label }} · {{ formatLineValue(tableauHover.point.value, tableauHover.valueType) }}{{ tableauPrimaryChart.unit }}</span><small>点击此节点可联动筛选</small></div>
              </div>
            </article>
            <article class="tableau-chart-sheet tableau-chart-sheet-wide">
            <div class="tableau-sheet-heading"><h2>24小时对比昨日增长</h2><span>累计实收金额 · {{ comparisonCutoffLabel }}</span></div>
            <div v-if="tableauSecondaryChart" class="tableau-chart-frame wide-chart-frame">
              <div class="tableau-axis-labels"><span>{{ tableauSecondaryChart.chart.maxLabel }}</span><span>{{ tableauSecondaryChart.chart.midLabel }}</span><span>0</span></div>
              <svg :viewBox="`0 0 ${tableauSecondaryChart.chart.width} 270`" preserveAspectRatio="none" role="img" aria-label="24小时对比昨日增长">
                <line v-for="grid in [0, 1, 2, 3]" :key="grid" x1="30" :y1="22 + grid * 72" x2="730" :y2="22 + grid * 72" class="tableau-grid-line" />
                <template v-for="series in tableauSecondaryChart.chart.series" :key="`sales-secondary-${series.key}`"><polyline :points="series.points.map((point) => `${point.x},${point.y + 8}`).join(' ')" class="tableau-comparison-line" :style="{ stroke: seriesIndexColor(series) }" /><template v-for="point in series.points" :key="`${series.key}-${point.hour}`"><circle :cx="point.x" :cy="point.y + 8" r="3" class="tableau-point-hit" :fill="seriesIndexColor(series)" tabindex="0" role="button" @mouseenter="showTableauPoint(tableauSecondaryChart, series, point, 'amount')" @mouseleave="clearTableauPoint" @focus="showTableauPoint(tableauSecondaryChart, series, point, 'amount')" @pointerdown="selectTableauPoint(tableauSecondaryChart, series, point, 'amount')" /><text :x="point.x" :y="Math.max(14, point.y - 1)" class="tableau-point-label" text-anchor="middle">{{ formatLineValue(point.value, 'amount') }}</text></template></template>
              </svg>
              <div class="tableau-x-axis"><span v-for="tick in tableauSecondaryChart.chart.xLabels" :key="tick.hour">{{ tick.label }}</span></div>
              <div v-if="tableauHover && tableauHover.chartId === tableauSecondaryChart.id" class="tableau-hover-card"><strong>{{ tableauHover.seriesLabel }}</strong><span>{{ tableauHover.point.label }} · {{ formatLineValue(tableauHover.point.value, tableauHover.valueType) }}{{ tableauSecondaryChart.unit }}</span><small>点击此节点可联动筛选</small></div>
            </div>
            </article>
          </div>
        </section>

        <section v-else class="tableau-canvas product-canvas">
          <div class="tableau-column tableau-column-left">
            <article class="tableau-sheet product-table-sheet">
              <div class="tableau-sheet-heading"><h2>商品对比昨日排名(商品数量)</h2><span>按编码数据对商品名称1降序排序</span></div>
              <table class="tableau-data-table">
                <thead><tr><th>商品名称1 / Sku编码</th><th>{{ tableauDateTotal.yesterday }}</th><th>{{ tableauDateTotal.today }}</th><th>差异%</th></tr></thead>
                <tbody><tr v-for="product in tableauProductRows" :key="product.product_no || product.product_name" @click="selectDashboardDimension('product', product.product_name)"><td><strong>{{ product.product_name }}</strong><small>{{ product.product_no || '无货号' }}<template v-if="product.spec_name"> · {{ product.spec_name }}</template></small></td><td>{{ formatUnits(product.yesterday_units) }}</td><td>{{ formatUnits(product.today_units) }}</td><td :class="growthClass(product.units_growth_pct)">{{ formatGrowth(product.units_growth_pct) }}</td></tr></tbody>
              </table>
            </article>
          </div>
          <div class="tableau-column tableau-column-right">
            <article class="tableau-chart-sheet">
              <div class="tableau-sheet-heading"><h2>24小时对比昨日波动(商品数量)</h2><span>付款时间 · 产品规格</span></div>
              <div v-if="tableauPrimaryChart" class="tableau-chart-frame">
                <div class="tableau-axis-labels"><span>{{ tableauPrimaryChart.chart.maxLabel }}</span><span>{{ tableauPrimaryChart.chart.midLabel }}</span><span>0</span></div>
                <svg :viewBox="`0 0 ${tableauPrimaryChart.chart.width} 270`" preserveAspectRatio="none" role="img" aria-label="24小时对比昨日波动(商品数量)">
                  <line v-for="grid in [0, 1, 2, 3]" :key="grid" x1="30" :y1="22 + grid * 72" x2="730" :y2="22 + grid * 72" class="tableau-grid-line" />
                  <template v-for="series in tableauPrimaryChart.chart.series" :key="`product-primary-${series.key}`"><polyline :points="series.points.map((point) => `${point.x},${point.y + 8}`).join(' ')" class="tableau-comparison-line" :style="{ stroke: seriesIndexColor(series) }" /><template v-for="point in series.points" :key="`${series.key}-${point.hour}`"><circle :cx="point.x" :cy="point.y + 8" r="3" class="tableau-point-hit" :fill="seriesIndexColor(series)" tabindex="0" role="button" @mouseenter="showTableauPoint(tableauPrimaryChart, series, point, 'units')" @mouseleave="clearTableauPoint" @focus="showTableauPoint(tableauPrimaryChart, series, point, 'units')" @pointerdown="selectTableauPoint(tableauPrimaryChart, series, point, 'units')" /><text :x="point.x" :y="Math.max(14, point.y - 1)" class="tableau-point-label" text-anchor="middle">{{ formatLineValue(point.value, 'units') }}</text></template></template>
                </svg>
                <div class="tableau-x-axis"><span v-for="tick in tableauPrimaryChart.chart.xLabels" :key="tick.hour">{{ tick.label }}</span></div>
                <div v-if="tableauHover && tableauHover.chartId === tableauPrimaryChart.id" class="tableau-hover-card"><strong>{{ tableauHover.seriesLabel }}</strong><span>{{ tableauHover.point.label }} · {{ formatLineValue(tableauHover.point.value, tableauHover.valueType) }}{{ tableauPrimaryChart.unit }}</span><small>点击此节点可联动筛选</small></div>
              </div>
            </article>
            <article class="tableau-chart-sheet tableau-chart-sheet-wide">
            <div class="tableau-sheet-heading"><h2>24小时对比昨日增长(商品数量)</h2><span>累计产品规格 · {{ comparisonCutoffLabel }}</span></div>
            <div v-if="tableauSecondaryChart" class="tableau-chart-frame wide-chart-frame">
              <div class="tableau-axis-labels"><span>{{ tableauSecondaryChart.chart.maxLabel }}</span><span>{{ tableauSecondaryChart.chart.midLabel }}</span><span>0</span></div>
              <svg :viewBox="`0 0 ${tableauSecondaryChart.chart.width} 270`" preserveAspectRatio="none" role="img" aria-label="24小时对比昨日增长(商品数量)">
                <line v-for="grid in [0, 1, 2, 3]" :key="grid" x1="30" :y1="22 + grid * 72" x2="730" :y2="22 + grid * 72" class="tableau-grid-line" />
                <template v-for="series in tableauSecondaryChart.chart.series" :key="`product-secondary-${series.key}`"><polyline :points="series.points.map((point) => `${point.x},${point.y + 8}`).join(' ')" class="tableau-comparison-line" :style="{ stroke: seriesIndexColor(series) }" /><template v-for="point in series.points" :key="`${series.key}-${point.hour}`"><circle :cx="point.x" :cy="point.y + 8" r="3" class="tableau-point-hit" :fill="seriesIndexColor(series)" tabindex="0" role="button" @mouseenter="showTableauPoint(tableauSecondaryChart, series, point, 'units')" @mouseleave="clearTableauPoint" @focus="showTableauPoint(tableauSecondaryChart, series, point, 'units')" @pointerdown="selectTableauPoint(tableauSecondaryChart, series, point, 'units')" /><text :x="point.x" :y="Math.max(14, point.y - 1)" class="tableau-point-label" text-anchor="middle">{{ formatLineValue(point.value, 'units') }}</text></template></template>
              </svg>
              <div class="tableau-x-axis"><span v-for="tick in tableauSecondaryChart.chart.xLabels" :key="tick.hour">{{ tick.label }}</span></div>
              <div v-if="tableauHover && tableauHover.chartId === tableauSecondaryChart.id" class="tableau-hover-card"><strong>{{ tableauHover.seriesLabel }}</strong><span>{{ tableauHover.point.label }} · {{ formatLineValue(tableauHover.point.value, tableauHover.valueType) }}{{ tableauSecondaryChart.unit }}</span><small>点击此节点可联动筛选</small></div>
            </div>
            </article>
          </div>
        </section>
      </section>

      <section v-if="false">
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
          <div><span class="eyebrow">看板筛选器</span><h3>统一筛选条件</h3><p>一组筛选器统一联动销售额、产品维度、折线图和排名。</p></div>
          <div class="filter-strip-actions"><span class="filter-count">已启用 {{ activeDashboardFilterCount }} 项</span><span v-if="dashboardFiltersDirty" class="filter-pending">待查询</span><button class="primary-button compact-button" type="button" :disabled="loading" @click="applyDashboardFilters"><Search :size="14" />按条件查询</button><button class="text-button" type="button" @click="clearDashboardFilters">清除看板筛选</button></div>
        </div>
          <div class="unified-filter-grid">
          <label class="filter-field"><span>平台筛选</span><select v-model="dashboardFilters.platformId" @change="markDashboardFiltersDirty"><option v-for="option in platformOptions" :key="option.id" :value="option.id">{{ option.name }}</option></select></label>
          <label class="filter-field"><span>时间字段</span><select v-model="dashboardFilters.timeType" @change="markDashboardFiltersDirty"><option v-for="option in timeOptions" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
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
          <div class="panel-heading"><div><h3>24 小时{{ activeTimeFieldLabel }}折线</h3><p>{{ hourlySeries.length || 2 }} 个日期的同小时趋势</p></div></div>
          <div class="hourly-legend"><span v-for="series in (hourlyLineCharts[0]?.chart.series || [])" :key="`legend-${series.key}`"><i class="legend-swatch" :style="{ background: series.color }"></i>{{ series.label }}</span><span class="legend-hint">{{ activeTimeFieldLabel }} · {{ comparisonCutoffLabel }}</span></div>
          <div v-if="hourlyRows.length" class="hourly-line-grid">
            <article v-for="line in hourlyLineCharts" :key="line.id" class="hourly-line-card">
              <div class="line-card-heading"><div><strong>{{ line.title }}</strong><span>{{ line.subtitle }}</span></div><div class="line-card-actions"><em>{{ line.unit }}</em><button class="chart-zoom-button" type="button" :aria-label="`放大${line.title}`" :title="`放大${line.title}`" @click="openLineChart(line)"><Maximize2 :size="14" /></button></div></div>
              <div class="line-chart">
                <div class="line-y-axis"><span>{{ line.chart.maxLabel }}</span><span>{{ line.chart.midLabel }}</span><span>{{ line.chart.zeroLabel }}</span></div>
                <svg :viewBox="`0 0 ${line.chart.width} ${line.chart.height}`" preserveAspectRatio="none" role="img" :aria-label="line.title">
                  <line v-for="grid in [0, 1, 2]" :key="grid" x1="30" :y1="16 + grid * 97" x2="730" :y2="16 + grid * 97" class="line-grid" />
                  <polyline v-for="series in line.chart.series" :key="`${line.id}-${series.key}`" :points="series.points.map((point) => `${point.x},${point.y}`).join(' ')" class="comparison-line" :style="{ stroke: series.color, strokeDasharray: series.dash || 'none' }" />
                  <g v-for="series in line.chart.series" :key="`${line.id}-points-${series.key}`">
                    <template v-for="point in series.points" :key="`${series.key}-${point.hour}`">
                      <template v-if="point.showPoint">
                        <circle :cx="point.x" :cy="point.y" r="2.6" class="line-point" :style="{ stroke: series.color }" />
                        <title>{{ point.label }}：{{ series.label }} {{ formatLineValue(point.value, line.id.includes('product') ? 'units' : 'amount') }}</title>
                      </template>
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
          <div class="hourly-legend modal-legend"><span v-for="series in (expandedLineChart.chart.series || [])" :key="`modal-legend-${series.key}`"><i class="legend-swatch" :style="{ background: series.color }"></i>{{ series.label }}</span><span class="legend-hint">点击右上角按钮或按 Esc 关闭</span></div>
          <div class="line-chart modal-line-chart">
            <div class="line-y-axis"><span>{{ expandedLineChart.chart.maxLabel }}</span><span>{{ expandedLineChart.chart.midLabel }}</span><span>{{ expandedLineChart.chart.zeroLabel }}</span></div>
            <svg :viewBox="`0 0 ${expandedLineChart.chart.width} ${expandedLineChart.chart.height}`" preserveAspectRatio="none" role="img" :aria-label="expandedLineChart.title">
              <line v-for="grid in [0, 1, 2]" :key="grid" x1="30" :y1="16 + grid * 97" x2="730" :y2="16 + grid * 97" class="line-grid" />
              <polyline v-for="series in expandedLineChart.chart.series" :key="`modal-${series.key}`" :points="series.points.map((point) => `${point.x},${point.y}`).join(' ')" class="comparison-line" :style="{ stroke: series.color, strokeDasharray: series.dash || 'none' }" />
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
          <div class="panel-heading"><div><h3>商品对比{{ comparisonYesterdayLabel }}排名</h3><p>按商品编码{{ comparisonTodayLabel }}实收金额排序</p></div><Package :size="18" class="panel-icon" /></div>
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
            <div v-for="(shop, index) in result.shops" :key="`${shop.shop_id}-${shop.shop_name}`" :data-shop-name="shop.shop_name" :class="['ranking-row', 'is-clickable', { 'is-selected': dashboardFilters.shopNames.includes(shop.shop_name) }]" role="button" tabindex="0" @click="selectDashboardDimension('shop', shop.shop_name)" @keydown.enter.prevent="selectDashboardDimension('shop', shop.shop_name)" @keydown.space.prevent="selectDashboardDimension('shop', shop.shop_name)">
              <b>{{ index + 1 }}</b><div class="ranking-main"><strong :title="shop.shop_name">{{ shop.shop_name }}</strong><div class="bar-track"><i :style="{ width: barWidth(shop.order_amount, maxShopAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatMoney(shop.order_amount) }}</strong><span>{{ formatNumber(shop.order_count) }} 单 · {{ shopShare(shop.order_amount) }}</span></div>
            </div>
            <div v-if="!result.shops.length" class="mini-empty">暂无店铺数据</div>
          </div>
        </article>

        <article class="panel ranking-panel">
          <div class="panel-heading"><div><h3>商品排行</h3><p>按商品行金额排序</p></div><Package :size="18" class="panel-icon" /></div>
          <div class="ranking-list">
            <div v-for="(product, index) in result.products" :key="`${product.product_no}-${product.spec_name}`" :data-product-name="product.product_name" :class="['ranking-row', 'is-clickable', { 'is-selected': dashboardFilters.productNames.includes(product.product_name) }]" role="button" tabindex="0" @click="selectDashboardDimension('product', product.product_name)" @keydown.enter.prevent="selectDashboardDimension('product', product.product_name)" @keydown.space.prevent="selectDashboardDimension('product', product.product_name)">
              <b>{{ index + 1 }}</b><div class="ranking-main"><strong :title="product.product_name">{{ product.product_name }}</strong><small>{{ product.product_no || '无货号' }}<template v-if="product.spec_name"> · {{ product.spec_name }}</template></small><div class="bar-track"><i class="orange" :style="{ width: barWidth(product.order_amount, maxProductAmount) }"></i></div></div><div class="ranking-value"><strong>{{ formatUnits(product.units) }} 件</strong><span>{{ formatMoney(product.order_amount) }}</span></div>
            </div>
            <div v-if="!result.products.length" class="mini-empty">暂无商品数据</div>
          </div>
        </article>
      </section>

      </section>
    </template>

    <section v-else-if="result && !hasOrders" class="empty-state panel">
      <Table2 :size="40" /><strong>当前条件没有匹配订单</strong><span>可以调整日期或平台后重新查询。</span>
    </section>
    <section v-else class="empty-state panel initial-empty">
      <div class="empty-icon"><BarChart3 :size="28" /></div><strong>准备开始分析</strong><span>选择近三天范围后点击“查询订单”，这里会展示 24 小时折线、店铺排行和商品排行。</span>
    </section>
  </main>
</template>
