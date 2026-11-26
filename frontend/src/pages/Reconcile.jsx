import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { GapBadge } from '../components/VersionCell'

export default function Reconcile() {
  const [data, setData] = useState(null)
  const [apps, setApps] = useState([])
  const [envs, setEnvs] = useState([])
  const [pick, setPick] = useState({ 应用编号: '', 环境编号: '' })
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    const [rec, appRows, envRows] = await Promise.all([api('/对账'), api('/应用'), api('/环境')])
    setData(rec)
    setApps(appRows)
    setEnvs(envRows)
    setPick((p) => ({
      应用编号: p.应用编号 || (appRows[0] ? String(appRows[0].编号) : ''),
      环境编号: p.环境编号 || (envRows[0] ? String(envRows[0].编号) : ''),
    }))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const s = data.发版统计 || {}

  const act = async (path, ok) => {
    setErr(''); setMsg('')
    try {
      const r = await api(path, { method: 'POST', body: JSON.stringify({}) })
      setMsg(ok(r))
      load()
    } catch (e) { setErr(e.message) }
  }
  const recompute = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api('/对账/重算', {
        method: 'POST',
        body: JSON.stringify({ 应用编号: Number(pick.应用编号), 环境编号: Number(pick.环境编号) }),
      })
      setMsg(r.缺口类型 ? `重算完成：缺口「${r.缺口类型}」（名册 ${r.名册版本} / 实例 ${r.实例汇总版本 || '无心跳'}）`
        : `重算完成：账实一致（名册 ${r.名册版本 || '空'}）`)
      load()
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="panel">
      <h2>发布对账（一套账：名册真源 × 实例附件）</h2>
      <div className="grid">
        {Object.entries(s).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
        <div className="stat"><div className="n">{data.晋升事件数}</div><div className="l">晋升事件数</div></div>
        <div className="stat"><div className="n">{data.开口缺口数}</div><div className="l">开口缺口数</div></div>
      </div>

      <div className="gap-panel">
        <h3>对账纠偏入口（按真源重算缺口）</h3>
        <div className="row" style={{ margin: 0 }}>
          <label>应用
            <select value={pick.应用编号} onChange={(e) => setPick({ ...pick, 应用编号: e.target.value })}>
              {apps.map((a) => <option key={a.编号} value={a.编号}>{a.名称}</option>)}
            </select>
          </label>
          <label>环境
            <select value={pick.环境编号} onChange={(e) => setPick({ ...pick, 环境编号: e.target.value })}>
              {envs.map((e) => <option key={e.编号} value={e.编号}>{e.名称}</option>)}
            </select>
          </label>
          <button type="button" className="secondary" onClick={recompute}>重算缺口</button>
        </div>
        {msg && <p className="muted" style={{ marginBottom: 0 }}>{msg}</p>}
        {err && <p className="err" style={{ marginBottom: 0 }}>{err}</p>}
      </div>

      <h2>缺口台账（半截双账必有标）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本</th><th>实例汇总</th><th>实例版本们</th><th>缺口</th><th>状态</th><th>处理</th></tr></thead>
          <tbody>
            {(data.缺口台账 || []).length === 0 && (
              <tr><td colSpan="8" className="muted">无开口缺口，名册与实例心跳一致。</td></tr>
            )}
            {(data.缺口台账 || []).map((g) => (
              <tr key={g.缺口编号}>
                <td>{g.应用}</td><td>{g.环境}</td>
                <td><span className="vcell-version">{g.名册版本 || '—'}</span></td>
                <td className={g.名册版本 !== g.实例汇总版本 ? 'vcell-obs drift' : ''}>{g.实例汇总版本 || '无心跳'}</td>
                <td>{g.实例版本们 || '-'}</td>
                <td><GapBadge type={g.缺口类型} status={g.缺口状态} /></td>
                <td>{g.缺口状态}</td>
                <td>
                  <div className="gap-actions">
                    <button type="button" className="ack"
                      onClick={() => act(`/api/缺口/${g.缺口编号}/确认`, () => '缺口已确认（名册未改动）')}>
                      确认挂账
                    </button>
                    <button type="button" className="correct" disabled={!g.可纠正}
                      title={g.可纠正 ? '实例已全员收敛，按实例汇总纠正名册' : '实例未收敛，禁止以附件盖真源'}
                      onClick={() => act(`/api/缺口/${g.缺口编号}/纠正`, () => '名册已按实例汇总纠正，缺口闭环')}>
                      纠正名册
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>名册与实例对读</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本（真源）</th><th>实例汇总（附件）</th><th>实例版本们</th><th>红灯数</th><th>缺口说明</th></tr></thead>
          <tbody>
            {(data.名册与实例 || []).map((r, i) => (
              <tr key={i}>
                <td>{r.应用}</td><td>{r.环境}</td>
                <td><span className="vcell-version">{r.名册版本 || '—'}</span></td>
                <td className={r.缺口类型 ? 'vcell-obs drift' : ''}>{r.实例汇总版本 || '无心跳'}</td>
                <td>{r.实例版本们}</td><td>{r.红灯数}</td>
                <td>{r.缺口类型 ? <GapBadge type={r.缺口类型} status={r.缺口状态} /> : <span className="muted">{r.名册版本 ? '版本一致' : '名册未登记版本'}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button></div>
    </div>
  )
}
