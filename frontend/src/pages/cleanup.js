import {table} from '../components/common/table.js';
import {esc} from '../utils/html.js';
import {session} from '../hooks/session.js';

const ITEM_STATE={pending:'待处理',done:'已完成',failed:'失败'};
const JOB_STATE={processing:'处理中',completed:'已完成'};

export function cleanupPage(store){
  const {duplicates,jobs}=store;
  const canWrite=session.user.role!=='viewer';
  const groupRows=duplicates.flatMap(g=>g.samples.map((s,idx)=>[
    idx===0?`<strong>${esc(g.normalized_key)}</strong><small>归一化重号组</small>`:'',
    `<strong>${esc(s.code)}</strong><small>#${s.id} · ${esc(s.title)}</small>`,
    s.keep?'<span class="badge completed">保留原号</span>':'<span class="badge running">改号</span>'
  ]));
  const jobRows=jobs.map(j=>[
    `<strong>清理批次 #${j.id}</strong><small>${esc(j.note)}</small>`,
    `<span class="badge ${j.status==='completed'?'completed':'running'}">${esc(JOB_STATE[j.status]||j.status)}</span>`,
    `${j.done_items} / ${j.total_items} 项${j.error?`<small class="err">${esc(j.error)}</small>`:''}`,
    j.status==='completed'
      ? '<span class="muted">受理已恢复</span>'
      : `<button class="link" data-run="${j.id}" ${canWrite?'':'disabled'}>续办 / 重试 →</button>`
  ]);
  const itemRows=jobs.flatMap(j=>(j.items||[]).filter(i=>i.action==='rename').map(i=>[
    `<strong>批次 #${j.id}</strong><small>${esc(i.original_code)}</small>`,
    `→ <strong>${esc(i.new_code||'')}</strong>`,
    `<span class="badge ${i.status==='done'?'completed':i.status==='failed'?'failed':'queued'}">${esc(ITEM_STATE[i.status]||i.status)}</span>${i.error?`<small class="err">${esc(i.error)}</small>`:''}`
  ]));
  return `<div class="split">
    <section>
      <div class="section-top"><h2>重号清理</h2><span>${duplicates.length} 组 / 按去空格、忽略大小写判定</span></div>
      ${duplicates.length
        ? table(['归一化编号','组内样本（每组保留首登记件）','处理方式'],groupRows)
        : '<div class="empty">当前没有重号。编号按“去掉首尾空格、忽略大小写”统一比对。</div>'}
      <div class="notice">规则：每组选一份保留原号，其余自动改号（原号-R01/-R02）。清理期间冲突号暂停受理、新号禁止抢注；完成后改号与恢复受理一起生效，原号各种写法只指向保留样本，旧移交仍归原样本。</div>
      <div class="section-top" style="margin-top:24px"><h2>清理批次</h2><span>${jobs.length} 批 / 中断后可续办</span></div>
      ${jobs.length?table(['批次','状态','进度','操作'],jobRows):'<div class="empty">还没有清理批次。</div>'}
      ${itemRows.length?`<div class="section-top" style="margin-top:24px"><h2>改号明细</h2><span>重试不重复改号</span></div>${table(['原编号','新编号','状态'],itemRows)}`:''}
    </section>
    <section class="form-panel">
      <span class="eyebrow">DUPLICATE CLEANUP</span>
      <h2>发起重号清理</h2>
      <form id="cleanup-form">
        <label>批次备注<input name="note" placeholder="首轮重号清理" value="重号清理批次"></label>
        <button class="primary" ${canWrite&&duplicates.length?'':'disabled'}>扫描并冻结冲突号 ↗</button>
      </form>
      <p class="help">建批即冻结冲突号并预占改号新号；每项独立事务推进，服务中断后点“续办 / 重试”即可，已改号自动跳过。</p>
      <div class="protocol">
        <h3>受理口径</h3>
        <ol>
          <li>编号先去首尾空格、再忽略大小写比对</li>
          <li>清理中：无关编号照常登记与移交</li>
          <li>冲突号 409 暂停受理，新号预占禁止抢注</li>
          <li>完成同事务恢复：原号写法归并到保留样本</li>
        </ol>
      </div>
    </section>
  </div>`;
}
