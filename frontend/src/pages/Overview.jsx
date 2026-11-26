import React, { useEffect, useState } from 'react'
import { api } from '../api'
import { VersionCell } from '../components/VersionCell'

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中</div>
  const d = data.分布 || {}
  const gapCls = data.缺口数 ? 'gap-panel' : 'gap-panel ok'
  return (
    <div className="panel">
      <h2>总览</h2>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>
      <div className={gapCls}>
        <h3>真源缺口（名册 vs 实例心跳）</h3>
        {data.缺口数 ? (
          <div className="row" style={{ margin: 0 }}>
            <span className="vcell-version">开口缺口 {data.缺口数} 个：</span>
            {Object.entries(data.缺口分类 || {}).map(([k, v]) => (
              <span key={k} className="gap-badge gap-behind">{k} × {v}</span>
            ))}
            <span className="muted">缺口只标注、不盖名册；处理入口在「对账」页。</span>
          </div>
        ) : <span className="muted">名册与实例心跳汇总全部一致，无开口缺口。</span>}
      </div>
      <h2>在途发版</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>发版单号</th><th>应用</th><th>目标版本</th><th>状态</th><th>当前环境（名册版本）</th><th>流水线</th></tr></thead>
          <tbody>
            {(data.在途 || []).map((r) => (
              <tr key={r.编号}>
                <td>{r.发版单号}</td><td>{r.应用}</td><td>{r.目标版本}</td><td>{r.状态}</td>
                <td>{r.当前环境 || '-'} · {r.当前环境名册版本 || '—'}</td>
                <td>{r.流水线}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>各环境版本（版本听名册）</h2>
      <div className="table-wrap">
        <table>
          <thead><tr><th>应用</th><th>环境</th><th>名册版本 / 实例汇总 / 缺口</th><th>版本更新时间</th><th>最近心跳</th></tr></thead>
          <tbody>
            {(data.环境版本 || []).map((v, i) => (
              <tr key={i}>
                <td>{v.应用}</td><td>{v.环境}</td>
                <td>
                  <VersionCell
                    version={v.名册版本}
                    observed={v.实例汇总版本}
                    gapType={v.缺口类型}
                    gapStatus={v.缺口状态}
                    gapTip={v.缺口说明}
                  />
                </td>
                <td>{v.更新时间 || '-'}</td><td>{v.最近心跳 || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
