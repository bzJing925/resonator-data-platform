import { C19_ID, C19_STRUCTURE_ID } from "./o14_contract.js";
import { importBrowserDataJson } from "./state.js";
import type { BrowserData, CandidateRow, FileDescriptor, MetricRow } from "./types.js";
import { parseBrowserDataJson } from "./validation.js";

let currentData: BrowserData | null = null;

function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function requiredElement(id: string): HTMLElement {
  const result = document.getElementById(id);
  if (!result) throw new Error(`缺少页面元素 #${id}`);
  return result;
}

function replaceContent(id: string, ...children: Node[]): void {
  requiredElement(id).replaceChildren(...children);
}

function code(value: unknown): HTMLElement {
  return element("code", undefined, value === null ? "null" : String(value));
}

function rawValue(value: unknown): Node {
  if (value === null) return element("span", "null-value", "null");
  if (value === "") return code('""');
  return code(value);
}

function displayValue(value: string | null, digits = 6): HTMLElement {
  if (value === null) return element("span", "null-value", "null");
  const result = element("span", "full-value");
  result.dataset.fullValue = value;
  result.title = `完整精度：${value}`;
  if (/^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(value) && Number.isFinite(Number(value))) {
    const rounded = Number(value).toFixed(digits).replace(/(?:\.0+|(?:(\.\d*?[1-9]))0+)$/, "$1");
    result.textContent = rounded;
  } else {
    result.textContent = value;
  }
  return result;
}

function definitionList(entries: Array<readonly [string, unknown]>, className = "field-list"): HTMLElement {
  const list = element("dl", className);
  for (const [label, value] of entries) {
    list.append(element("dt", undefined, label));
    const definition = element("dd");
    definition.append(rawValue(value));
    list.append(definition);
  }
  return list;
}

function summaryCards(entries: Array<readonly [string, Node]>): HTMLElement {
  const list = element("dl", "summary-grid");
  for (const [label, value] of entries) {
    const card = element("div", "summary-card");
    card.append(element("dt", undefined, label));
    const definition = element("dd");
    definition.append(value);
    card.append(definition);
    list.append(card);
  }
  return list;
}

function renderDecision(data: BrowserData): void {
  const decision = data.decision;
  const cards = summaryCards([
    ["历史基线 k²（percent_number）", displayValue(decision.baseline.value)],
    ["绝对候选目标 k²（非提升量）", displayValue(decision.absolute_target.value)],
    ["所需提升（percentage_points）", displayValue(decision.required_uplift.value)],
    ["当前推荐 / 等级", code("0 / grade: null")],
  ]);
  const note = element("p", "decision-note");
  note.append(
    element("strong", undefined, `${decision.scientific_status} · ${decision.decision} · ${decision.reason}。`),
    document.createTextNode(" eligible_models=[]；无当前推荐；A/B/C 数量均为 0；trusted_for_unmeasured_optimization=false。"),
  );
  const evidenceHeading = element("h3", undefined, "保守证据：全部为 null，原因完整保留");
  const wrap = element("div", "table-wrap");
  const table = element("table");
  table.setAttribute("aria-label", "保守证据空值与原因");
  const caption = element("caption", undefined, "缺失不是 0，不是通过，也不表示所有历史点预测均低于目标");
  const head = element("thead");
  const headRow = element("tr");
  for (const label of ["字段", "当前值", "null 原因"]) headRow.append(element("th", undefined, label));
  head.append(headRow);
  const body = element("tbody");
  const evidence = decision.conservative_evidence;
  const rows = [
    ["conservative_value", evidence.conservative_value, evidence.conservative_value_null_reasons],
    ["error_margin", evidence.error_margin, evidence.error_margin_null_reasons],
    ["extrapolation_distance", evidence.extrapolation_distance, evidence.extrapolation_distance_null_reasons],
    ["testable_max_distance", evidence.testable_max_distance, evidence.testable_max_distance_null_reasons],
  ] as const;
  for (const [name, value, reasons] of rows) {
    const row = element("tr");
    row.append(element("td", undefined, name));
    const valueCell = element("td");
    valueCell.append(rawValue(value));
    row.append(valueCell, element("td", undefined, reasons.join("；")));
    body.append(row);
  }
  table.append(caption, head, body);
  wrap.append(table);
  replaceContent("decision-content", cards, note, evidenceHeading, wrap);
}

function candidateDetails(row: CandidateRow, columns: string[]): HTMLElement {
  const details = element("details");
  details.append(element("summary", undefined, "完整字段与来源（33 列）"));
  const entries = columns.map((column) => [column, Reflect.get(row, column)] as const);
  details.append(definitionList(entries));
  const warning = element("p", "muted", "legacy_recommendation_text 是历史文案，不是当前推荐；model_prediction_provenance 明确记录本轮未新增、未重算预测。");
  details.append(warning);
  return details;
}

function renderCandidates(data: BrowserData): void {
  const fragment = document.createDocumentFragment();
  for (const [index, candidate] of data.candidates.entries()) {
    const row = element("tr", candidate.control_type === "negative_control" ? "negative-control" : undefined);
    row.dataset.candidateRow = String(index + 1);
    row.dataset.candidateId = candidate.candidate_id;
    row.dataset.controlType = candidate.control_type;
    row.append(element("td", undefined, String(index + 1)));
    row.append(element("td", "candidate-id", candidate.candidate_id));
    const control = element("td");
    control.append(element("span", candidate.control_type === "negative_control" ? "badge danger" : "badge neutral", candidate.control_type));
    row.append(control);
    const geometry = element("td", "geometry");
    geometry.append(
      code(`EG=${candidate.eg_um} μm`), element("br"),
      code(`FL=${candidate.fl_um} μm`), element("br"),
      code(`A=${candidate.area_um2} μm² · PF=${candidate.has_pf}`), element("br"),
      code(candidate.branch_id),
    );
    row.append(geometry);
    const support = element("td");
    support.append(code(candidate.support_label), element("br"), code(candidate.source_row_key));
    row.append(support);
    const grade = element("td");
    grade.append(element("span", "null-value", "grade: null"), element("br"), code(candidate.grade_null_reason));
    row.append(grade);
    const detailCell = element("td");
    detailCell.append(candidateDetails(candidate, data.candidate_columns));
    row.append(detailCell);
    fragment.append(row);
  }
  replaceContent("candidate-rows", fragment);
}

function metricSourceDetails(metric: MetricRow): HTMLElement {
  const details = element("details", "source-detail");
  details.append(element("summary", undefined, "来源与完整精度"));
  details.append(definitionList([
    ["value_full_precision", metric.value],
    ["source_file_id", metric.source_file_id],
    ["source_sha256", metric.source_sha256],
    ["source_row_key", metric.source_row_key],
    ["scope", metric.scope],
    ["support_label", metric.support_label],
    ["evidence_status", metric.evidence_status],
    ["null_reason", metric.null_reason],
  ], "source-detail"));
  return details;
}

function metricRow(metric: MetricRow, index: number, fullPrecision: boolean): HTMLElement {
  const row = element("tr");
  row.dataset.metricRow = String(index + 1);
  row.dataset.candidateId = metric.candidate_id;
  row.append(element("td", undefined, String(index + 1)));
  row.append(element("td", "candidate-id", metric.candidate_id));
  row.append(element("td", undefined, metric.model_id), element("td", undefined, metric.metric));
  const value = element("td");
  value.append(fullPrecision ? rawValue(metric.value) : displayValue(metric.value));
  row.append(value, element("td", undefined, metric.unit));
  const status = element("td");
  if (metric.evidence_status === "registered_historical_reference") {
    const label = metric.source_file_id === "P3_STRUCTURES"
      ? "同结构实测提参历史值（非当前推荐）"
      : "历史模型参考（非当前推荐）";
    status.append(element("span", "badge history", label));
  } else {
    status.append(element("span", "null-value", "当前证据不足"), element("br"), code(metric.null_reason));
  }
  row.append(status);
  const source = element("td");
  source.append(metricSourceDetails(metric));
  row.append(source);
  return row;
}

function renderMetrics(data: BrowserData): void {
  const fragment = document.createDocumentFragment();
  for (const [index, metric] of data.metrics.entries()) fragment.append(metricRow(metric, index, false));
  replaceContent("metric-rows", fragment);
}

function renderC19(data: BrowserData): void {
  const candidate = data.candidates.find((row) => row.candidate_id === C19_ID);
  if (!candidate) throw new Error("C19 缺失");
  const measured = JSON.parse(candidate.measured_geometry_match) as {
    status: string;
    match_count: number;
    structure_ids: string[];
  };
  const geometry = definitionList([
    ["candidate_id", candidate.candidate_id],
    ["control_type", candidate.control_type],
    ["geometry", `EG=${candidate.eg_um} μm；FL=${candidate.fl_um} μm；A=${candidate.area_um2} μm²；PF=${candidate.has_pf}`],
    ["measured_geometry_match.status", measured.status],
    ["match_count", measured.match_count],
    ["structure_ids", measured.structure_ids.join(", ")],
    ["support_label", candidate.support_label],
    ["exclusion_reason", candidate.exclusion_reason],
  ]);
  const metricsWrap = element("div");
  metricsWrap.append(element("h3", undefined, "同结构实测提参值与历史模型预测分列（完整精度）"));
  const wrap = element("div", "table-wrap");
  const table = element("table");
  table.setAttribute("aria-label", "C19 同结构实测值与历史模型预测");
  table.append(element("caption", undefined, `${C19_STRUCTURE_ID}：Qs/BodeQ 为同结构提参值；k²/Qp 为历史模型预测（非当前推荐）`));
  const thead = element("thead");
  const header = element("tr");
  for (const label of ["#", "候选 ID", "模型", "指标", "值", "单位", "证据状态 / null 原因", "完整来源"]) header.append(element("th", undefined, label));
  thead.append(header);
  const tbody = element("tbody");
  const c19Metrics = data.metrics.filter((metric) => metric.candidate_id === C19_ID);
  c19Metrics.forEach((metric, index) => {
    const row = metricRow(metric, index, true);
    const statusCell = row.children.item(6);
    if (statusCell) {
      const label = metric.source_file_id === "P3_STRUCTURES"
        ? "同结构实测提参值（几何精确匹配）"
        : metric.value === null ? "当前证据不足" : "历史模型预测（非当前推荐）";
      statusCell.replaceChildren(element("span", metric.value === null ? "null-value" : "badge history", label));
      if (metric.null_reason) statusCell.append(element("br"), code(metric.null_reason));
    }
    tbody.append(row);
  });
  table.append(thead, tbody);
  wrap.append(table);
  metricsWrap.append(wrap);
  const layout = element("div", "c19-layout");
  layout.append(geometry, metricsWrap);
  replaceContent("c19-content", layout);
}

function renderProcesses(data: BrowserData): void {
  const { neighborhood, random_process: random, search_status: search } = data.decision;
  const grid = element("div", "process-grid");
  const processes = [
    ["邻域 / 九点预测", [["status", neighborhood.status], ["null_reason", neighborhood.null_reason], ["nine_point_prediction_executed", neighborhood.nine_point_prediction_executed]]],
    ["随机过程", [["status", random.status], ["reason", random.reason], ["seed", random.seed], ["seed_used", random.seed_used], ["random_draw_count", random.random_draw_count], ["bootstrap_prediction_count", random.bootstrap_prediction_count], ["refit_count", random.refit_count]]],
    ["搜索", [["search_status", search], ["recommendation_count", data.decision.recommendation_count], ["trusted_for_unmeasured_optimization", data.decision.trusted_for_unmeasured_optimization]]],
  ] as const;
  for (const [title, entries] of processes) {
    const card = element("section", "process-card");
    card.append(element("h3", undefined, title));
    const list = element("dl");
    for (const [label, value] of entries) {
      list.append(element("dt", undefined, label));
      const definition = element("dd");
      definition.append(rawValue(value));
      list.append(definition);
    }
    card.append(list);
    grid.append(card);
  }
  replaceContent("process-content", grid);
}

function renderSources(data: BrowserData): void {
  const groups: Array<readonly [string, FileDescriptor[]]> = [
    ["O14 交付", data.source_traceability.o14_delivery],
    ["T15 清单", [data.source_traceability.t15_manifest]],
    ["登记输入", data.source_traceability.registered_inputs],
  ];
  const fragment = document.createDocumentFragment();
  for (const [group, descriptors] of groups) {
    for (const descriptor of descriptors) {
      const row = element("tr");
      row.dataset.sourceFileId = descriptor.file_id;
      row.append(element("td", undefined, group), element("td", undefined, descriptor.file_id));
      const path = element("td", "source-path");
      path.append(code(descriptor.path));
      row.append(path, element("td", undefined, String(descriptor.bytes)));
      const hash = element("td");
      hash.append(element("code", "hash", descriptor.sha256));
      row.append(hash);
      fragment.append(row);
    }
  }
  replaceContent("source-rows", fragment);
}

function render(data: BrowserData): void {
  renderDecision(data);
  renderC19(data);
  renderCandidates(data);
  renderMetrics(data);
  renderProcesses(data);
  renderSources(data);
}

function importMessage(kind: "success" | "error", message: string): void {
  const status = requiredElement("import-message");
  status.className = `import-message ${kind}`;
  status.setAttribute("role", kind === "error" ? "alert" : "status");
  status.textContent = message;
}

async function handleImport(input: HTMLInputElement): Promise<void> {
  const file = input.files?.[0];
  if (!file || !currentData) return;
  try {
    if (file.size > 5 * 1024 * 1024) throw new Error("文件超过 5 MiB 离线导入上限");
    const text = await file.text();
    const result = importBrowserDataJson(currentData, text);
    if (!result.accepted) {
      const errors = result.validation.ok ? [] : result.validation.errors;
      const suffix = errors.length > 4 ? `；另有 ${errors.length - 4} 项错误` : "";
      importMessage("error", `已拒绝导入，当前有效报告保持不变：${errors.slice(0, 4).join("；")}${suffix}`);
      return;
    }
    currentData = result.data;
    render(currentData);
    importMessage("success", "导入校验通过：20 个候选、160 条指标及来源哈希均符合 O14/O15 契约。");
  } catch (error) {
    const message = error instanceof Error ? error.message : "未知读取错误";
    importMessage("error", `已拒绝导入，当前有效报告保持不变：${message}`);
  } finally {
    input.value = "";
  }
}

function start(): void {
  const input = requiredElement("json-import");
  if (!(input instanceof HTMLInputElement)) throw new Error("导入控件类型错误");
  input.addEventListener("change", () => { void handleImport(input); });
  const embedded = requiredElement("browser-data").textContent;
  if (embedded === null) throw new Error("内嵌数据为空");
  const result = parseBrowserDataJson(embedded);
  if (!result.ok) {
    importMessage("error", `内嵌数据校验失败：${result.errors.join("；")}`);
    return;
  }
  currentData = result.data;
  render(currentData);
  importMessage("success", "内嵌数据校验通过：20 个候选、160 条指标；完全离线，无外部请求。");
}

try {
  start();
} catch (error) {
  const message = error instanceof Error ? error.message : "未知初始化错误";
  importMessage("error", `报告初始化失败：${message}`);
}
