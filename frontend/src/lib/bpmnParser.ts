export type BpmnNodeType =
  | "startEvent"
  | "endEvent"
  | "userTask"
  | "serviceTask"
  | "task"
  | "exclusiveGateway"
  | "parallelGateway";

export interface BpmnNode {
  id: string;
  label: string;
  stepKey: string;
  type: BpmnNodeType;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BpmnEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface BpmnGraphModel {
  processName: string;
  nodes: BpmnNode[];
  edges: BpmnEdge[];
}

export interface BpmnWidgetConfig {
  process_name?: string;
  step_field?: string;
  wait_field?: string;
  assignee_field?: string;
  assignee_role_field?: string;
  request_id_field?: string;
  request_title_field?: string;
  status_field?: string;
  sla_field?: string;
  entered_at_field?: string;
  department_field?: string;
  pending_status_values?: string[];
  heatmap_enabled?: boolean;
  heatmap_metric?: "avg_wait" | "max_wait" | "pending_count";
  bpmn_xml?: string;
  nodes?: BpmnNode[];
  edges?: BpmnEdge[];
  step_mapping?: Record<string, string>;
}

export interface BpmnCartableItem {
  requestId: string;
  requestTitle: string;
  stepName: string;
  assigneeName: string;
  assigneeRole: string;
  department: string;
  status: string;
  isPending: boolean;
  waitHours: number;
  slaHours: number;
  isOverdue: boolean;
  enteredAt: string;
  rawRow: Record<string, any>;
}

export interface BpmnAssigneeSummary {
  assigneeName: string;
  assigneeRole: string;
  pendingCount: number;
  totalCount: number;
  avgWaitHours: number;
  maxWaitHours: number;
  overdueCount: number;
  items: BpmnCartableItem[];
}

export interface BpmnStepMetrics {
  stepKey: string;
  stepLabel: string;
  pendingCount: number;
  completedCount: number;
  totalCount: number;
  avgWaitHours: number;
  maxWaitHours: number;
  slaHours: number;
  overdueCount: number;
  heatRatio: number; // 0..1 normalized across steps
  isBottleneck: boolean;
  items: BpmnCartableItem[];
  pendingItems: BpmnCartableItem[];
  assignees: BpmnAssigneeSummary[];
}

const DEFAULT_PENDING_STATUSES = [
  "pending",
  "waiting",
  "in_progress",
  "in progress",
  "overdue",
  "open",
  "active",
  "در انتظار اقدام",
  "در انتظار",
  "دارای تاخیر",
  "دارای تأخیر",
  "در حال انجام",
  "معوق",
  "باز",
];

function stripNs(tag: string): string {
  const idx = tag.indexOf(":");
  return idx >= 0 ? tag.slice(idx + 1) : tag;
}

function getAllElementsByLocalName(root: Document | Element, localNames: string[]): Element[] {
  const wanted = new Set(localNames.map((n) => n.toLowerCase()));
  const out: Element[] = [];
  const all = root.getElementsByTagName("*");
  for (let i = 0; i < all.length; i++) {
    const el = all[i];
    if (wanted.has(stripNs(el.tagName).toLowerCase())) {
      out.push(el);
    }
  }
  return out;
}

function applyAutoLayout(nodes: BpmnNode[], edges: BpmnEdge[]): BpmnNode[] {
  if (nodes.length === 0) return nodes;
  const incomingCount = new Map<string, number>();
  const adj = new Map<string, string[]>();
  nodes.forEach((n) => {
    incomingCount.set(n.id, 0);
    adj.set(n.id, []);
  });
  edges.forEach((e) => {
    if (adj.has(e.source) && incomingCount.has(e.target)) {
      adj.get(e.source)!.push(e.target);
      incomingCount.set(e.target, (incomingCount.get(e.target) || 0) + 1);
    }
  });

  // Topological BFS layering
  const layer = new Map<string, number>();
  const queue: string[] = [];
  nodes.forEach((n) => {
    if ((incomingCount.get(n.id) || 0) === 0 || n.type === "startEvent") {
      layer.set(n.id, 0);
      queue.push(n.id);
    }
  });
  if (queue.length === 0) {
    layer.set(nodes[0].id, 0);
    queue.push(nodes[0].id);
  }
  const visited = new Set<string>(queue);
  while (queue.length > 0) {
    const cur = queue.shift()!;
    const curL = layer.get(cur) || 0;
    for (const nxt of adj.get(cur) || []) {
      if (!visited.has(nxt)) {
        visited.add(nxt);
        layer.set(nxt, curL + 1);
        queue.push(nxt);
      }
    }
  }
  nodes.forEach((n, idx) => {
    if (!layer.has(n.id)) layer.set(n.id, idx);
  });

  const byLayer = new Map<number, BpmnNode[]>();
  nodes.forEach((n) => {
    const l = layer.get(n.id) || 0;
    if (!byLayer.has(l)) byLayer.set(l, []);
    byLayer.get(l)!.push(n);
  });

  const laidOut: BpmnNode[] = [];
  const xStart = 50;
  const xStep = 230;
  const yCenter = 165;

  nodes.forEach((n) => {
    const l = layer.get(n.id) || 0;
    const peers = byLayer.get(l) || [n];
    const pIdx = peers.findIndex((p) => p.id === n.id);
    const totalH = (peers.length - 1) * 135;
    const x = xStart + l * xStep;
    const y = yCenter - totalH / 2 + pIdx * 135;
    laidOut.push({ ...n, x, y });
  });
  return laidOut;
}

export function parseBpmnXml(xmlText: string): BpmnGraphModel {
  const trimmed = (xmlText || "").trim();
  if (!trimmed) {
    return { processName: "BPMN Process", nodes: [], edges: [] };
  }

  // Browser DOMParser when available, regex fallback for SSR/Node
  if (typeof DOMParser !== "undefined") {
    const doc = new DOMParser().parseFromString(trimmed, "text/xml");
    const parseErr = doc.getElementsByTagName("parsererror");
    if (parseErr.length === 0) {
      const procEls = getAllElementsByLocalName(doc, ["process"]);
      const processName = procEls[0]?.getAttribute("name") || procEls[0]?.getAttribute("id") || "BPMN Process";

      const shapeBounds = new Map<string, { x: number; y: number; width: number; height: number }>();
      for (const shape of getAllElementsByLocalName(doc, ["BPMNShape"])) {
        const elId = shape.getAttribute("bpmnElement");
        if (!elId) continue;
        const bounds = getAllElementsByLocalName(shape, ["Bounds"])[0];
        if (bounds) {
          shapeBounds.set(elId, {
            x: Number(bounds.getAttribute("x") || 0),
            y: Number(bounds.getAttribute("y") || 0),
            width: Number(bounds.getAttribute("width") || 160),
            height: Number(bounds.getAttribute("height") || 80),
          });
        }
      }

      const nodeTagMap: Record<string, BpmnNodeType> = {
        startevent: "startEvent",
        endevent: "endEvent",
        usertask: "userTask",
        servicetask: "serviceTask",
        task: "task",
        manualtask: "userTask",
        scripttask: "serviceTask",
        businessruletask: "serviceTask",
        subprocess: "task",
        callactivity: "task",
        exclusivegateway: "exclusiveGateway",
        parallelgateway: "parallelGateway",
        inclusivegateway: "exclusiveGateway",
      };

      const rawNodes: BpmnNode[] = [];
      const seenNodeIds = new Set<string>();
      const nodeEls = getAllElementsByLocalName(doc, Object.keys(nodeTagMap));
      for (const el of nodeEls) {
        const id = el.getAttribute("id") || "";
        if (!id || seenNodeIds.has(id)) continue;
        seenNodeIds.add(id);
        const local = stripNs(el.tagName).toLowerCase();
        const nType = nodeTagMap[local] || "task";
        const label = (el.getAttribute("name") || id).trim();
        const b = shapeBounds.get(id);
        const isEvent = nType === "startEvent" || nType === "endEvent";
        const isGw = nType === "exclusiveGateway" || nType === "parallelGateway";
        rawNodes.push({
          id,
          label,
          stepKey: label,
          type: nType,
          x: b ? b.x : 0,
          y: b ? b.y : 0,
          width: b ? Math.max(b.width, isEvent ? 44 : isGw ? 52 : 168) : isEvent ? 44 : isGw ? 52 : 176,
          height: b ? Math.max(b.height, isEvent ? 44 : isGw ? 52 : 84) : isEvent ? 44 : isGw ? 52 : 88,
        });
      }

      const edges: BpmnEdge[] = [];
      for (const flow of getAllElementsByLocalName(doc, ["sequenceFlow"])) {
        const id = flow.getAttribute("id") || `flow_${edges.length + 1}`;
        const source = flow.getAttribute("sourceRef") || "";
        const target = flow.getAttribute("targetRef") || "";
        const label = flow.getAttribute("name") || undefined;
        if (source && target && seenNodeIds.has(source) && seenNodeIds.has(target)) {
          edges.push({ id, source, target, label });
        }
      }

      const hasCoords = rawNodes.some((n) => shapeBounds.has(n.id));
      const nodes = hasCoords ? rawNodes : applyAutoLayout(rawNodes, edges);
      return { processName, nodes, edges };
    }
  }

  // Regex fallback when DOMParser is unavailable
  const nodes: BpmnNode[] = [];
  const edges: BpmnEdge[] = [];
  const tagRegex = /<(?:[a-zA-Z0-9_-]+:)?(startEvent|endEvent|userTask|serviceTask|task|exclusiveGateway|parallelGateway)\b([^>]*)/gi;
  let m: RegExpExecArray | null;
  while ((m = tagRegex.exec(trimmed)) !== null) {
    const typeRaw = m[1] as BpmnNodeType;
    const attrs = m[2] || "";
    const idM = attrs.match(/\bid=["']([^"']+)["']/);
    const nameM = attrs.match(/\bname=["']([^"']+)["']/);
    if (!idM) continue;
    const isEvent = typeRaw === "startEvent" || typeRaw === "endEvent";
    const isGw = typeRaw === "exclusiveGateway" || typeRaw === "parallelGateway";
    const label = (nameM?.[1] || idM[1]).trim();
    nodes.push({
      id: idM[1],
      label,
      stepKey: label,
      type: typeRaw,
      x: 0,
      y: 0,
      width: isEvent ? 44 : isGw ? 52 : 176,
      height: isEvent ? 44 : isGw ? 52 : 88,
    });
  }
  const flowRegex = /<(?:[a-zA-Z0-9_-]+:)?sequenceFlow\b([^>]*)/gi;
  while ((m = flowRegex.exec(trimmed)) !== null) {
    const attrs = m[1] || "";
    const idM = attrs.match(/\bid=["']([^"']+)["']/);
    const srcM = attrs.match(/\bsourceRef=["']([^"']+)["']/);
    const tgtM = attrs.match(/\btargetRef=["']([^"']+)["']/);
    const nameM = attrs.match(/\bname=["']([^"']+)["']/);
    if (srcM && tgtM) {
      edges.push({
        id: idM?.[1] || `flow_${edges.length + 1}`,
        source: srcM[1],
        target: tgtM[1],
        label: nameM?.[1],
      });
    }
  }
  const procMatch = trimmed.match(/<(?:[a-zA-Z0-9_-]+:)?process\b([^>]*)/i);
  const procNameM = procMatch?.[1]?.match(/\bname=["']([^"']+)["']/);
  return {
    processName: procNameM?.[1]?.trim() || "BPMN Process",
    nodes: applyAutoLayout(nodes, edges),
    edges,
  };
}

export function buildAutoBpmnGraphFromSteps(
  steps: string[],
  processName = "فرآیند گردش کار (BPMN)"
): BpmnGraphModel {
  const uniqueSteps = Array.from(new Set(steps.map((s) => String(s || "").trim()).filter(Boolean)));
  const nodes: BpmnNode[] = [];
  const edges: BpmnEdge[] = [];

  const startId = "start_1";
  nodes.push({
    id: startId,
    label: "شروع",
    stepKey: "__start__",
    type: "startEvent",
    x: 40,
    y: 120,
    width: 44,
    height: 44,
  });

  let prevId = startId;
  uniqueSteps.forEach((step, idx) => {
    const nid = `step_${idx + 1}`;
    const col = idx % 4;
    const row = Math.floor(idx / 4);
    nodes.push({
      id: nid,
      label: step,
      stepKey: step,
      type: "userTask",
      x: 135 + col * 225,
      y: 96 + row * 150,
      width: 184,
      height: 92,
    });
    edges.push({
      id: `edge_${prevId}_${nid}`,
      source: prevId,
      target: nid,
    });
    prevId = nid;
  });

  const endId = "end_1";
  const lastNode = nodes[nodes.length - 1];
  nodes.push({
    id: endId,
    label: "پایان",
    stepKey: "__end__",
    type: "endEvent",
    x: (lastNode?.x || 135) + 225,
    y: (lastNode?.y || 96) + 24,
    width: 44,
    height: 44,
  });
  edges.push({
    id: `edge_${prevId}_${endId}`,
    source: prevId,
    target: endId,
  });

  return { processName, nodes, edges };
}

export const DEFAULT_PROCUREMENT_BPMN_GRAPH: BpmnGraphModel = {
  processName: "فرآیند تأمین، بررسی فنی و تصویب مالی (BPMN 2.0)",
  nodes: [
    { id: "n_start", label: "شروع", stepKey: "__start__", type: "startEvent", x: 32, y: 128, width: 44, height: 44 },
    { id: "n_submit", label: "ثبت درخواست خرید", stepKey: "ثبت درخواست خرید", type: "userTask", x: 112, y: 102, width: 178, height: 96 },
    { id: "n_tech", label: "بررسی فنی و برآورد", stepKey: "بررسی فنی و برآورد", type: "userTask", x: 326, y: 102, width: 178, height: 96 },
    { id: "n_gw1", label: "تأیید مدارک؟", stepKey: "__gw_tech__", type: "exclusiveGateway", x: 540, y: 124, width: 52, height: 52 },
    { id: "n_rev", label: "اصلاح یا تکمیل مستندات", stepKey: "اصلاح یا تکمیل مستندات", type: "userTask", x: 480, y: 248, width: 178, height: 96 },
    { id: "n_fin", label: "تأیید بودجه و مالی", stepKey: "تأیید بودجه و مالی", type: "userTask", x: 634, y: 102, width: 178, height: 96 },
    { id: "n_comm", label: "کمیسیون معاملات و مناقصه", stepKey: "کمیسیون معاملات و مناقصه", type: "userTask", x: 850, y: 102, width: 184, height: 96 },
    { id: "n_po", label: "صدور سفارش و پرداخت نهایی", stepKey: "صدور سفارش و پرداخت نهایی", type: "serviceTask", x: 1070, y: 102, width: 182, height: 96 },
    { id: "n_end", label: "پایان", stepKey: "__end__", type: "endEvent", x: 1288, y: 128, width: 44, height: 44 },
  ],
  edges: [
    { id: "e1", source: "n_start", target: "n_submit" },
    { id: "e2", source: "n_submit", target: "n_tech" },
    { id: "e3", source: "n_tech", target: "n_gw1" },
    { id: "e4", source: "n_gw1", target: "n_fin", label: "تأیید" },
    { id: "e5", source: "n_gw1", target: "n_rev", label: "نقص مدرک" },
    { id: "e6", source: "n_rev", target: "n_tech", label: "ارسال مجدد" },
    { id: "e7", source: "n_fin", target: "n_comm" },
    { id: "e8", source: "n_comm", target: "n_po" },
    { id: "e9", source: "n_po", target: "n_end" },
  ],
};

export type BpmnColumnDescriptor = string | { name: string; label?: string };

export function inferBpmnFieldMapping(
  columns: BpmnColumnDescriptor[],
  config?: BpmnWidgetConfig
): Required<
  Pick<
    BpmnWidgetConfig,
    | "step_field"
    | "wait_field"
    | "assignee_field"
    | "assignee_role_field"
    | "request_id_field"
    | "request_title_field"
    | "status_field"
    | "sla_field"
    | "entered_at_field"
    | "department_field"
  >
> {
  const entries = columns.map((c) =>
    typeof c === "string"
      ? { name: c, label: c }
      : { name: c.name, label: c.label || c.name }
  );
  const colNames = entries.map((e) => e.name);

  const findCol = (preferred: string | undefined, patterns: string[], fallback = ""): string => {
    if (preferred && colNames.includes(preferred)) return preferred;
    for (const pat of patterns) {
      const p = pat.toLowerCase();
      const hit = entries.find(
        (e) => e.name.toLowerCase() === p || e.label.toLowerCase() === p
      );
      if (hit) return hit.name;
    }
    for (const pat of patterns) {
      const p = pat.toLowerCase();
      const hit = entries.find(
        (e) => e.name.toLowerCase().includes(p) || e.label.toLowerCase().includes(p)
      );
      if (hit) return hit.name;
    }
    return fallback;
  };

  return {
    step_field: findCol(config?.step_field, ["step_name", "step", "activity", "stage", "task", "گام", "مرحله", "فعالیت"], colNames[0] || "step_name"),
    wait_field: findCol(config?.wait_field, ["wait_hours", "wait_time", "duration_hours", "waiting_hours", "wait", "duration", "انتظار", "مدت", "تاخیر", "توقف", "ساعت"], ""),
    assignee_field: findCol(config?.assignee_field, ["assignee_name", "assignee", "owner", "user", "کارتابل", "مسئول", "کاربر", "شخص", "نام"], ""),
    assignee_role_field: findCol(config?.assignee_role_field, ["assignee_role", "role", "سمت", "نقش"], ""),
    request_id_field: findCol(config?.request_id_field, ["request_code", "request_id", "case_id", "ticket_id", "id", "کد", "شناسه"], ""),
    request_title_field: findCol(config?.request_title_field, ["request_title", "title", "subject", "description", "عنوان", "موضوع"], ""),
    status_field: findCol(config?.status_field, ["status", "state", "وضعیت"], ""),
    sla_field: findCol(config?.sla_field, ["sla_hours", "sla", "target_hours", "سقف", "مهلت", "مجاز"], ""),
    entered_at_field: findCol(config?.entered_at_field, ["entered_at", "created_at", "timestamp", "date", "تاریخ", "ورود"], ""),
    department_field: findCol(config?.department_field, ["department", "unit", "واحد", "بخش"], ""),
  };
}

export function isRowPendingStatus(statusRaw: any, customPendingValues?: string[]): boolean {
  const s = String(statusRaw ?? "").trim().toLowerCase();
  if (!s) return true; // If no status column exists, treat rows in step as active
  if (["completed", "done", "closed", "approved", "finished", "تکمیل‌شده", "تکمیل شده", "پایان‌یافته", "بسته"].some((k) => s.includes(k))) {
    return false;
  }
  const pool = (customPendingValues && customPendingValues.length > 0 ? customPendingValues : DEFAULT_PENDING_STATUSES).map((x) =>
    x.toLowerCase()
  );
  return pool.some((k) => s.includes(k));
}

export function computeBpmnAnalytics(
  rows: Record<string, any>[],
  columns: string[],
  config?: BpmnWidgetConfig,
  heatmapMetric: "avg_wait" | "max_wait" | "pending_count" = "avg_wait"
): {
  graph: BpmnGraphModel;
  stepMap: Record<string, BpmnStepMetrics>;
  maxMetricValue: number;
  minMetricValue: number;
  totalPendingRequests: number;
  overallAvgWaitHours: number;
  bottleneckStep: BpmnStepMetrics | null;
  mapping: ReturnType<typeof inferBpmnFieldMapping>;
} {
  const mapping = inferBpmnFieldMapping(columns, config);
  const stepMapping = config?.step_mapping || {};

  // Build or resolve BPMN Graph
  let graph: BpmnGraphModel;
  if (config?.nodes && config.nodes.length > 0) {
    const normNodes: BpmnNode[] = config.nodes.map((n: any, idx: number) => ({
      id: String(n.id || `node_${idx}`),
      label: String(n.label || n.name || n.id || ""),
      stepKey: String(n.stepKey || n.label || n.name || n.id || ""),
      type: (n.type || "userTask") as BpmnNodeType,
      x: Number(n.x ?? 40 + idx * 190),
      y: Number(n.y ?? 120),
      width: Number(n.width ?? 178),
      height: Number(n.height ?? 92),
    }));
    const normEdges: BpmnEdge[] = (config.edges || []).map((e: any, idx: number) => ({
      id: String(e.id || `edge_${idx}`),
      source: String(e.source || e.sourceRef || ""),
      target: String(e.target || e.targetRef || ""),
      label: e.label || e.name || undefined,
    }));
    graph = {
      processName: config.process_name || "فرآیند BPMN",
      nodes: normNodes,
      edges: normEdges,
    };
  } else if (config?.bpmn_xml) {
    graph = parseBpmnXml(config.bpmn_xml);
    if (config.process_name) graph.processName = config.process_name;
  } else {
    const distinctSteps = Array.from(
      new Set(rows.map((r) => String(r[mapping.step_field] ?? "").trim()).filter(Boolean))
    );
    const defaultTaskKeys = new Set(
      DEFAULT_PROCUREMENT_BPMN_GRAPH.nodes
        .filter((n) => n.type !== "startEvent" && n.type !== "endEvent" && n.type !== "exclusiveGateway" && n.type !== "parallelGateway")
        .map((n) => n.stepKey)
    );
    const matchesDefault = distinctSteps.length > 0 && distinctSteps.every((s) => defaultTaskKeys.has(s));
    if (matchesDefault) {
      graph = DEFAULT_PROCUREMENT_BPMN_GRAPH;
    } else {
      graph = buildAutoBpmnGraphFromSteps(distinctSteps, config?.process_name || "گردش فرآیند (BPMN)");
    }
  }

  // Group rows by normalized step key
  const itemsByStep = new Map<string, BpmnCartableItem[]>();
  rows.forEach((r, idx) => {
    const rawStep = String(r[mapping.step_field] ?? "").trim();
    if (!rawStep) return;
    const mappedStep = stepMapping[rawStep] || rawStep;
    const waitHours = mapping.wait_field ? Number(r[mapping.wait_field] ?? 0) || 0 : 0;
    const slaHours = mapping.sla_field ? Number(r[mapping.sla_field] ?? 24) || 24 : 24;
    const statusStr = mapping.status_field ? String(r[mapping.status_field] ?? "در انتظار اقدام") : "در انتظار اقدام";
    const isPending = mapping.status_field ? isRowPendingStatus(statusStr, config?.pending_status_values) : true;
    const assigneeName = mapping.assignee_field ? String(r[mapping.assignee_field] ?? "").trim() || "تخصیص‌نیافته" : "کارتابل عمومی";
    const assigneeRole = mapping.assignee_role_field ? String(r[mapping.assignee_role_field] ?? "").trim() : "";
    const requestId = mapping.request_id_field ? String(r[mapping.request_id_field] ?? `REQ-${idx + 1}`) : `REQ-${idx + 1}`;
    const requestTitle = mapping.request_title_field ? String(r[mapping.request_title_field] ?? "").trim() : "";
    const department = mapping.department_field ? String(r[mapping.department_field] ?? "").trim() : "";
    const enteredAt = mapping.entered_at_field ? String(r[mapping.entered_at_field] ?? "").trim() : "";
    const isOverdue = waitHours > slaHours || statusStr.includes("تاخیر") || statusStr.includes("تأخیر") || statusStr.toLowerCase().includes("overdue");

    const item: BpmnCartableItem = {
      requestId,
      requestTitle,
      stepName: mappedStep,
      assigneeName,
      assigneeRole,
      department,
      status: statusStr,
      isPending,
      waitHours,
      slaHours,
      isOverdue,
      enteredAt,
      rawRow: r,
    };
    if (!itemsByStep.has(mappedStep)) itemsByStep.set(mappedStep, []);
    itemsByStep.get(mappedStep)!.push(item);
  });

  // Compute metrics for each task node in the graph
  const taskNodes = graph.nodes.filter(
    (n) => !["startEvent", "endEvent", "exclusiveGateway", "parallelGateway"].includes(n.type)
  );
  const stepMap: Record<string, BpmnStepMetrics> = {};
  const metricValues: number[] = [];

  for (const node of taskNodes) {
    const matchedItems =
      itemsByStep.get(node.stepKey) ||
      itemsByStep.get(node.label) ||
      itemsByStep.get(node.id) ||
      [];
    const pendingItems = matchedItems
      .filter((it) => it.isPending)
      .sort((a, b) => b.waitHours - a.waitHours);
    const completedCount = matchedItems.length - pendingItems.length;
    const statPool = pendingItems.length > 0 ? pendingItems : matchedItems;
    const avgWaitHours =
      statPool.length > 0
        ? Math.round((statPool.reduce((acc, it) => acc + it.waitHours, 0) / statPool.length) * 10) / 10
        : 0;
    const maxWaitHours =
      statPool.length > 0
        ? Math.round(Math.max(...statPool.map((it) => it.waitHours)) * 10) / 10
        : 0;
    const slaHours =
      statPool.length > 0
        ? Math.round(statPool.reduce((acc, it) => acc + it.slaHours, 0) / statPool.length)
        : 24;
    const overdueCount = pendingItems.filter((it) => it.isOverdue).length;

    // Group pending (or all) items by assignee for drill-down view
    const byAssignee = new Map<string, BpmnCartableItem[]>();
    const drillPool = pendingItems.length > 0 ? pendingItems : matchedItems;
    for (const it of drillPool) {
      if (!byAssignee.has(it.assigneeName)) byAssignee.set(it.assigneeName, []);
      byAssignee.get(it.assigneeName)!.push(it);
    }
    const assignees: BpmnAssigneeSummary[] = Array.from(byAssignee.entries())
      .map(([assigneeName, list]) => {
        const sorted = [...list].sort((a, b) => b.waitHours - a.waitHours);
        const pCount = sorted.filter((x) => x.isPending).length;
        const avgW = Math.round((sorted.reduce((s, x) => s + x.waitHours, 0) / sorted.length) * 10) / 10;
        const maxW = Math.round(Math.max(...sorted.map((x) => x.waitHours)) * 10) / 10;
        return {
          assigneeName,
          assigneeRole: sorted[0]?.assigneeRole || "",
          pendingCount: pCount,
          totalCount: sorted.length,
          avgWaitHours: avgW,
          maxWaitHours: maxW,
          overdueCount: sorted.filter((x) => x.isOverdue).length,
          items: sorted,
        };
      })
      .sort((a, b) => b.pendingCount - a.pendingCount || b.avgWaitHours - a.avgWaitHours);

    const mVal =
      heatmapMetric === "max_wait"
        ? maxWaitHours
        : heatmapMetric === "pending_count"
        ? pendingItems.length
        : avgWaitHours;
    if (matchedItems.length > 0) metricValues.push(mVal);

    const metrics: BpmnStepMetrics = {
      stepKey: node.stepKey,
      stepLabel: node.label,
      pendingCount: pendingItems.length,
      completedCount,
      totalCount: matchedItems.length,
      avgWaitHours,
      maxWaitHours,
      slaHours,
      overdueCount,
      heatRatio: 0,
      isBottleneck: false,
      items: matchedItems,
      pendingItems,
      assignees,
    };
    stepMap[node.id] = metrics;
    stepMap[node.stepKey] = metrics;
  }

  const maxMetricValue = metricValues.length > 0 ? Math.max(...metricValues) : 0;
  const minMetricValue = metricValues.length > 0 ? Math.min(...metricValues) : 0;

  let bottleneckStep: BpmnStepMetrics | null = null;
  let totalPendingRequests = 0;
  let weightedWaitSum = 0;
  let totalWeight = 0;

  for (const node of taskNodes) {
    const m = stepMap[node.id];
    if (!m) continue;
    const rawVal =
      heatmapMetric === "max_wait"
        ? m.maxWaitHours
        : heatmapMetric === "pending_count"
        ? m.pendingCount
        : m.avgWaitHours;
    const span = Math.max(1e-6, maxMetricValue - minMetricValue);
    m.heatRatio = m.totalCount === 0 ? 0 : maxMetricValue === minMetricValue ? (rawVal > 0 ? 0.5 : 0) : (rawVal - minMetricValue) / span;
    m.isBottleneck = m.pendingCount > 0 && (m.heatRatio >= 0.72 || m.avgWaitHours > m.slaHours * 1.15);

    totalPendingRequests += m.pendingCount;
    const w = m.pendingCount > 0 ? m.pendingCount : m.totalCount;
    weightedWaitSum += m.avgWaitHours * w;
    totalWeight += w;

    if (
      m.totalCount > 0 &&
      (!bottleneckStep ||
        m.avgWaitHours > bottleneckStep.avgWaitHours ||
        (m.avgWaitHours === bottleneckStep.avgWaitHours && m.pendingCount > bottleneckStep.pendingCount))
    ) {
      bottleneckStep = m;
    }
  }

  return {
    graph,
    stepMap,
    maxMetricValue,
    minMetricValue,
    totalPendingRequests,
    overallAvgWaitHours: totalWeight > 0 ? Math.round((weightedWaitSum / totalWeight) * 10) / 10 : 0,
    bottleneckStep,
    mapping,
  };
}
