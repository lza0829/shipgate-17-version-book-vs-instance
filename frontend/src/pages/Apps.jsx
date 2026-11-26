import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { GapBadge } from '../components/VersionCell'

export default function Apps() {
  const [rows, setRows] = useState([])
  const [envs, setEnvs] = useState([])
  const [name, setName] = useState('')
  const [owner, setOwner] = useState('')
  const [err, setErr] = useState('')
  const load = async () => {
    const [apps, envRows] = await Promise.all([api('/应用'), api('/环境')])
    setRows(apps)
    setEnvs(envRows)
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const create = async () => {
    setErr('')
    try {
      await api('/应用', { method: 'POST', body: JSON.stringify({ 名称: name, 负责人: owner, 状态: '启用' }) })
      setName(''); setOwner(''); load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>应用名册（版本真源）</h2>
      <p className="muted">环境当前版本以名册为准；实例心跳仅作探活附件，不一致时挂缺口标，不盖名册版本。</p>
      {err && <p className="err">{err}</p>}
      <div className="row">
        <label>名称<input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>负责人<input value={owner} onChange={(e) => setOwner(e.target.value)} /></label>
        <button type="button" onClick={create}>新建应用</button>
        <button type="button" className="secondary" onClick={() => load().catch((e) => setErr(e.message))}>刷新</button>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>编号</th><th>名称</th><th>负责人</th><th>状态</th>
              {envs.map((e) => <th key={e.编号}>{e.名称}版本（名册）</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.编号}>
                <td>{r.编号}</td><td>{r.名称}</td><td>{r.负责人}</td><td>{r.状态}</td>
                {envs.map((e) => {
                  const cell = (r.环境版本 || []).find((v) => v.环境编号 === e.编号)
                  return (
                    <td key={e.编号}>
                      {cell ? (
                        <div className="vcell">
                          <div className="vcell-reg">
                            <span className="vcell-version">{cell.名册版本 || '—'}</span>
                            <GapBadge type={cell.缺口类型} status={cell.缺口状态} />
                          </div>
                          <div className={`vcell-obs${cell.缺口类型 ? ' drift' : ''}`}>
                            实例汇总：{cell.实例汇总版本 || '无心跳'}（{cell.实例数}）
                          </div>
                        </div>
                      ) : '—'}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
