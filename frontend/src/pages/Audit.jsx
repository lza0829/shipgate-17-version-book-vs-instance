import React, { useEffect, useState } from 'react'
import { api } from '../api'

const KINDS = ['', '系统', '应用', '发版单', '晋升', '回滚', '心跳', '探针', '缺口', '放行窗', '回滚窗', '设置']

export default function Audit() {
  const [rows, setRows] = useState([])
  const [kind, setKind] = useState('')
  const [ref, setRef] = useState('')
  const [err, setErr] = useState('')
  const load = () => {
    const params = new URLSearchParams()
    if (kind) params.set('类别', kind)
    if (ref) params.set('关联编号', ref)
    const qs = params.toString()
    return api(`/审计${qs ? `?${qs}` : ''}`).then(setRows).catch((e) => setErr(e.message))
  }
  useEffect(() => { load() }, [kind, ref])
  return (
    <div className="panel">
      <h2>审计流水（跟名册真源留痕）</h2>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>类别
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => <option key={k} value={k}>{k || '全部'}</option>)}
          </select>
        </label>
        <label>关联编号<input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="如缺口编号" /></label>
        <button type="button" className="secondary" onClick={load}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>类别</th><th>动作</th><th>关联</th><th>备注</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}><td>{r.时间}</td><td>{r.类别}</td><td>{r.动作}</td><td>{r.关联编号 || '-'}</td><td>{r.备注}</td></tr>
            ))}
            {rows.length === 0 && <tr><td colSpan="5" className="muted">无记录</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}
