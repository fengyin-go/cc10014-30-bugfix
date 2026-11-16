import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/backup'
const columns = [
  "策略编号",
  "目标服务",
  "备份周期",
  "保留份数",
  "存储介质",
  "加密开关",
  "恢复演练",
  "过期清理"
]
const actions = [
  "提交校验",
  "启用备份",
  "停用备份"
]
const statuses = [
  "草稿",
  "待校验",
  "已启用",
  "已停用"
]
const stats = [
  {
    "label": "备份策略总数",
    "value": 0
  },
  {
    "label": "已启用策略",
    "value": 0
  },
  {
    "label": "待校验策略",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

type ArchiveSnapshot = {
  契约名称: string
  版本: number
  生成时间: string
  校验状态: string
  已归档数量: number
  快照: { 记录编号: string; status: string; 归档时间: string }[]
}

export default function BackupView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [snapshot, setSnapshot] = useState<ArchiveSnapshot | null>(null)
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) {
        throw new Error('备份策略列表读取失败')
      }
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据备份列表读取失败')
    }
    try {
      // 备份模块只读取日志归档契约中已完成校验的数据快照
      const snapshotRes = await request(`${ENDPOINT}/log-archive-snapshot`)
      setSnapshot(snapshotRes.ok ? ((await snapshotRes.json()) as ArchiveSnapshot) : null)
    } catch {
      setSnapshot(null)
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error('数据备份动作未生效，请稍后重试')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据备份操作失败')
    }
  }

  return (
    <section className="page" data-module="backup">
      <header className="page-head">
        <div>
          <h2>数据备份管理</h2>
          <p className="page-desc">维护数据备份策略的输入、执行约束与结果记录，为数据备份提供可验证的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('备份策略登记入口尚未接入审批流')}>
            登记备份策略
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据备份清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
        <article className="stat-card">
          <span className="stat-label">日志归档契约快照</span>
          <strong className="stat-value">
            {snapshot ? `${snapshot.已归档数量} 条已校验` : '暂无'}
          </strong>
          {snapshot && (
            <span className="stat-label">
              {snapshot.契约名称} · v{snapshot.版本} · {snapshot.校验状态} · {snapshot.生成时间}
            </span>
          )}
        </article>
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {actions.map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无数据备份数据，可先登记备份策略</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条数据备份记录</span>
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
