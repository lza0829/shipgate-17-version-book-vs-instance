import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { GapBadge } from '../components/VersionCell'

export default function Health() {
  const [rows, setRows] = useState([])
  const [apps, setApps] = useState([])
  const [envs, setEnvs] = useState([])
  const [form, setForm] = useState({ 应用编号: '', 环境编号: '', 版本: '', 实例键: '主实例', 健康: '探针绿' })
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const load = async () => {
    const [inst, appRows, envRows] = await Promise.all([api('/实例'), api('/应用'), api('/环境')])
    setRows(inst)
    setApps(appRows)
    setEnvs(envRows)
    setForm((f) => ({
      ...f,
      应用编号: f.应用编号 || (appRows[0] ? String(appRows[0].编号) : ''),
      环境编号: f.环境编号 || (envRows[0] ? String(envRows[0].编号) : ''),
    }))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const setH = async (id, health) => {
    setErr(''); setMsg('')
    try {
      await api(`/实例/${id}/健康`, { method: 'POST', body: JSON.stringify({ 健康: health }) })
      load()
    } catch (e) { setErr(e.message) }
  }
  const beat = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api('/实例/心跳', {
        method: 'POST',
        body: JSON.stringify({
          应用编号: Number(form.应用编号),
          环境编号: Number(form.环境编号),
          版本: form.版本,
          实例键: form.实例键 || '主实例',
          健康: form.健康,
        }),
      })
      setMsg(`心跳已登记（实例附件）；名册版本仍为 ${r.名册版本}${r.缺口类型 ? `，缺口：${r.缺口类型}` : '，账实一致'}`)
      setForm((f) => ({ ...f, 版本: '' }))
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>健康探针 · 实例册（探活附件）</h2>
      <p className="muted">心跳只登记实例册，永不改名册；与名册不符时自动挂缺口标，去「对账」页确认或纠正。</p>
      {err && <p className="err">{err}</p>}
      {msg && <p className="muted">{msg}</p>}
      <div className="heartbeat-form">
        <h3>实例心跳登记</h3>
        <div className="row">
          <label>应用
            <select value={form.应用编号} onChange={(e) => setForm({ ...form, 应用编号: e.target.value })}>
              {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
            </select>
          </label>
          <label>环境
            <select value={form.环境编号} onChange={(e) => setForm({ ...form, 环境编号: e.target.value })}>
              {envs.map((e) => <option key={e.编号} value={e.编号}>{e.名称}</option>)}
            </select>
          </label>
          <label>实例键<input value={form.实例键} onChange={(e) => setForm({ ...form, 实例键: e.target.value })} placeholder="主实例" /></label>
          <label>心跳版本<input value={form.版本} onChange={(e) => setForm({ ...form, 版本: e.target.value })} placeholder="1.4.0" /></label>
          <label>探针
            <select value={form.健康} onChange={(e) => setForm({ ...form, 健康: e.target.value })}>
              <option value="探针绿">探针绿</option>
              <option value="探针红">探针红</option>
            </select>
          </label>
          <button type="button" onClick={beat}>登记心跳</button>
        </div>
      </div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>实例键</th><th>心跳版本</th><th>名册版本（真源）</th><th>缺口</th><th>健康</th><th>发版单</th><th>操作</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.应用}</td><td>{r.环境}</td><td>{r.实例键}</td>
                <td>{r.版本}</td>
                <td><span className="vcell-version">{r.名册版本 || '—'}</span></td>
                <td><GapBadge type={r.缺口类型} status={r.缺口状态} /></td>
                <td>{r.健康}</td><td>{r.发版单编号 || '-'}</td>
                <td>
                  <button type="button" onClick={() => setH(r.编号, '探针绿')}>探针绿</button>{' '}
                  <button type="button" className="danger" onClick={() => setH(r.编号, '探针红')}>探针红</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button></div>
    </div>
  )
}
