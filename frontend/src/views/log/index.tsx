import { useCallback, useEffect, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/log'
const columns = [
  "记录编号",
  "调用服务",
  "请求时间",
  "响应耗时",
  "返回码",
  "调用方IP",
  "请求大小KB",
  "响应大小KB"
]
const statuses = [
  "草稿",
  "记录中",
  "已归档",
  "已清理"
]
// 生命周期单步动作：草稿→记录中→已归档→已清理，恢复逐级回退
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  '草稿': ['开始记录'],
  '记录中': ['归档记录'],
  '已归档': ['清理日志', '恢复日志'],
  '已清理': ['恢复日志'],
}
const READONLY_ACTIONS = ['导出日志']
const REQUIRED_FIELDS = ["记录编号", "调用服务", "请求时间"]

type Row = Record<string, string | number | boolean | null>

type Stats = {
  日志记录总数: number
  记录中日志: number
  已归档记录: number
  已清理记录: number
  待处理: number
  归档清单数量: number
  审计记录数: number
}

const emptyStats: Stats = {
  日志记录总数: 0,
  记录中日志: 0,
  已归档记录: 0,
  已清理记录: 0,
  待处理: 0,
  归档清单数量: 0,
  审计记录数: 0,
}

export default function LogView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [stats, setStats] = useState<Stats>(emptyStats)
  const [archiveRows, setArchiveRows] = useState<Row[]>([])
  const [archiveTotal, setArchiveTotal] = useState(0)
  const [auditRows, setAuditRows] = useState<Row[]>([])
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [keyword, setKeyword] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [createForm, setCreateForm] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams()
    if (keyword) query.set('keyword', keyword)
    if (statusFilter) query.set('status', statusFilter)
    try {
      const [listRes, statsRes, archiveRes, auditRes] = await Promise.all([
        request(`${ENDPOINT}?${query.toString()}`),
        request(`${ENDPOINT}/stats`),
        request(`${ENDPOINT}/archive?status=${encodeURIComponent('已归档')}&size=50`),
        request(`${ENDPOINT}/audit?size=50`),
      ])
      if (!listRes.ok) throw new Error('日志记录列表读取失败')
      const payload = await listRes.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
      if (statsRes.ok) setStats({ ...emptyStats, ...(await statsRes.json()) })
      if (archiveRes.ok) {
        const archive = await archiveRes.json()
        setArchiveRows(archive.items ?? [])
        setArchiveTotal(archive.total ?? 0)
      }
      if (auditRes.ok) {
        const audit = await auditRes.json()
        setAuditRows((audit.items ?? []).slice(-8).reverse())
      }
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调用日志列表读取失败')
    }
  }, [keyword, statusFilter])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ values: { action } }),
      })
      if (!response.ok) {
        throw new Error('调用日志动作未生效，请稍后重试')
      }
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '动作被拦截')
      } else {
        setNotice(payload.message ?? '操作完成')
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '调用日志操作失败')
    }
  }

  async function createEntry() {
    if (submitting) return
    setSubmitting(true)
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(ENDPOINT, {
        method: 'POST',
        body: JSON.stringify({ values: createForm }),
      })
      const payload = await response.json()
      if (!payload.ok) {
        setErrorMessage(payload.message ?? '登记失败')
      } else {
        setNotice(payload.message ?? '日志记录已登记')
        setCreateForm({})
      }
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '日志记录登记失败')
    } finally {
      setSubmitting(false)
    }
  }

  const statCards = [
    { label: '日志记录总数', value: stats.日志记录总数 },
    { label: '记录中日志', value: stats.记录中日志 },
    { label: '已归档记录', value: stats.已归档记录 },
    { label: '待处理', value: stats.待处理 },
  ]

  return (
    <section className="page" data-module="log">
      <header className="page-head">
        <div>
          <h2>调用日志管理</h2>
          <p className="page-desc">维护调用日志记录的输入、执行约束与结果记录，为调用日志提供可验证的数据工程流程。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出调用日志清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {statCards.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void createEntry()
        }}
      >
        {REQUIRED_FIELDS.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={createForm[field] ?? ''}
              placeholder={`登记${field}`}
              onChange={(event) => setCreateForm((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn primary" type="submit" disabled={submitting}>
          登记日志记录
        </button>
      </form>

      <form
        className="filter-bar"
        onSubmit={(event) => {
          event.preventDefault()
          void reload()
        }}
      >
        <label className="filter-item">
          <span>记录编号</span>
          <input
            value={keyword}
            placeholder="按记录编号检索"
            onChange={(event) => setKeyword(event.target.value)}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            {statuses.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => {
            setKeyword('')
            setStatusFilter('')
          }}
        >
          重置条件
        </button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>状态</th>
            <th>待处理</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={String(row.id)}>
              {columns.map((column) => <td key={column}>{String(row[column] ?? '—')}</td>)}
              <td>{String(row.status ?? '—')}</td>
              <td>{row.pending ? '待处理' : '—'}</td>
              <td className="row-actions">
                {[...(ACTIONS_BY_STATUS[String(row.status)] ?? []), ...READONLY_ACTIONS].map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 3}>暂无调用日志数据，可先登记日志记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">归档清单（调用日志归档数据集，共 {archiveTotal} 条）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>记录编号</th>
            <th>调用服务</th>
            <th>请求时间</th>
            <th>状态</th>
            <th>归档时间</th>
            <th>归档窗口</th>
          </tr>
        </thead>
        <tbody>
          {archiveRows.map((row) => (
            <tr key={String(row.id)}>
              <td>{String(row['记录编号'] ?? '—')}</td>
              <td>{String(row['调用服务'] ?? '—')}</td>
              <td>{String(row['请求时间'] ?? '—')}</td>
              <td>{String(row.status ?? '—')}</td>
              <td>{String(row['归档时间'] ?? '—')}</td>
              <td>{String(row['归档窗口'] ?? '—')}</td>
            </tr>
          ))}
          {archiveRows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={6}>归档清单为空，日志归档后在此核对</td>
            </tr>
          )}
        </tbody>
      </table>

      <h3 className="section-title">审计记录（共 {stats.审计记录数} 条，保留原始操作详情）</h3>
      <table className="data-table">
        <thead>
          <tr>
            <th>序号</th>
            <th>时间</th>
            <th>记录编号</th>
            <th>动作</th>
            <th>状态流转</th>
            <th>操作人</th>
          </tr>
        </thead>
        <tbody>
          {auditRows.map((row) => (
            <tr key={String(row.seq)}>
              <td>{String(row.seq ?? '—')}</td>
              <td>{String(row['时间'] ?? '—')}</td>
              <td>{String(row['记录编号'] ?? '—')}</td>
              <td>{String(row['动作'] ?? '—')}</td>
              <td>{`${String(row['原状态'] ?? '—')} → ${String(row['新状态'] ?? '—')}`}</td>
              <td>{String(row['操作人'] ?? '—')}</td>
            </tr>
          ))}
          {auditRows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={6}>暂无审计记录</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条调用日志记录</span>
        {notice && <span className="notice-text">{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>
    </section>
  )
}
