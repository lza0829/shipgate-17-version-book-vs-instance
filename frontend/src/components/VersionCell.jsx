import React from 'react'

// 缺口标：全局唯一展示口径。版本列永远显示名册真源，缺口只作标注不盖版本。
const KIND_CLASS = {
  实例缺失: 'gap-missing',
  实例未达版本: 'gap-behind',
  实例版本混杂: 'gap-mixed',
}

export function GapBadge({ type, status }) {
  if (!type) return null
  const cls = KIND_CLASS[type] || 'gap-behind'
  const suffix = status === '已确认' ? '（已确认）' : ''
  return <span className={`gap-badge ${cls}`} title={`${type}${suffix}`}>{type}{suffix}</span>
}

// 版本单元格：名册版本（真源）+ 实例汇总（附件）+ 缺口标
export function VersionCell({ version, observed, gapType, gapStatus, gapTip }) {
  const drift = gapType && observed && observed !== version
  return (
    <div className="vcell">
      <div className="vcell-reg">
        <span className="vcell-version">{version || '—'}</span>
        <GapBadge type={gapType} status={gapStatus} />
      </div>
      <div className={`vcell-obs${drift ? ' drift' : ''}`}>
        实例汇总：{observed || '无心跳'}
        {gapTip ? <span className="vcell-tip"> · {gapTip}</span> : null}
      </div>
    </div>
  )
}
