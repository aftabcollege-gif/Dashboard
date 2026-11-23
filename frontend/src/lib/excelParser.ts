import readXlsxFile from "read-excel-file/universal";

export interface ParsedExcelColumn {
  field_name: string;
  original_name: string;
  display_name: string;
  data_type: "integer" | "float" | "boolean" | "date" | "string";
  semantic_role: "measure" | "dimension" | "time";
  nullable: boolean;
}

export interface ParsedExcelResult {
  sheets: string[];
  selected_sheet: string;
  total_rows: number;
  duplicate_count: number;
  columns: ParsedExcelColumn[];
  preview_rows: Record<string, any>[];
  all_rows: Record<string, any>[];
}

function normalizeFaDigits(val: string): string {
  return val
    .replace(/[۰-۹]/g, (d) => String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)))
    .replace(/[٠-٩]/g, (d) => String("٠١٢٣٤٥٦٧٨٩".indexOf(d)))
    .replace(/ي/g, "ی")
    .replace(/ى/g, "ی")
    .replace(/ك/g, "ک");
}

function tryParseNumber(val: any): number | null {
  if (typeof val === "number" && Number.isFinite(val)) return val;
  if (typeof val !== "string") return null;
  const cleaned = normalizeFaDigits(val).trim().replace(/[,،\s]/g, "");
  if (!cleaned || cleaned === "-" || cleaned === ".") return null;
  if (/^-?\d+(\.\d+)?$/.test(cleaned)) {
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function isDateLike(val: any): boolean {
  if (val instanceof Date && !Number.isNaN(val.getTime())) return true;
  if (typeof val !== "string") return false;
  const s = normalizeFaDigits(val).trim();
  return /^\d{4}[-/]\d{1,2}[-/]\d{1,2}/.test(s) || /^\d{1,2}[-/]\d{1,2}[-/]\d{4}/.test(s);
}

function inferType(values: any[], overrideType?: string): "integer" | "float" | "boolean" | "date" | "string" {
  if (overrideType && ["integer", "float", "boolean", "date", "string"].includes(overrideType)) {
    return overrideType as any;
  }
  const nonNull = values.filter((v) => v !== null && v !== undefined && String(v).trim() !== "");
  if (nonNull.length === 0) return "string";

  if (nonNull.every((v) => typeof v === "boolean" || ["true", "false"].includes(String(v).trim().toLowerCase()))) {
    return "boolean";
  }
  let intCount = 0;
  let numCount = 0;
  let dateCount = 0;
  for (const v of nonNull.slice(0, 100)) {
    const num = tryParseNumber(v);
    if (num !== null) {
      numCount++;
      if (Number.isInteger(num)) intCount++;
    } else if (isDateLike(v)) {
      dateCount++;
    }
  }
  const sampleLen = Math.min(nonNull.length, 100);
  if (numCount >= sampleLen * 0.85) {
    return intCount === numCount ? "integer" : "float";
  }
  if (dateCount >= sampleLen * 0.8) {
    return "date";
  }
  return "string";
}

function inferSemanticRole(colLabel: string, dtype: string): "measure" | "dimension" | "time" {
  const low = normalizeFaDigits(colLabel).toLowerCase();
  if (dtype === "date") return "time";
  if (
    ["year", "month", "quarter", "day", "date", "timestamp", "logged_at", "created_at", "سال", "ماه", "فصل", "تاریخ", "زمان"].some(
      (k) => low === k || low.includes("تاریخ") || low.includes("timestamp")
    )
  ) {
    return "time";
  }
  if (
    (dtype === "integer" || dtype === "float") &&
    !["کد", "شناسه", "id", "code", "شماره", "سال", "status_code", "http_status", "line_number", "port", "pid"].some(
      (k) => low === k || low.endsWith("_id")
    )
  ) {
    return "measure";
  }
  return "dimension";
}

const APACHE_LOG_RE =
  /^(\S+)\s+\S+\s+(\S+)\s+\[([^\]]+)\]\s+"([A-Z]+)\s+(\S+)[^"]*"\s+(\d{3})\s+(\d+|-)(?:\s+"([^"]*)"\s+"([^"]*)")?/;
const TS_LEVEL_LOG_RE =
  /^(\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+\[?(TRACE|DEBUG|INFO|NOTICE|WARN|WARNING|ERROR|CRITICAL|FATAL)\]?\s+(?:\[([^\]]+)\]\s+)?(.*)$/i;
const KV_PAIR_RE = /\b([A-Za-z_][A-Za-z0-9_.-]*)=("[^"]*"|\S+)/g;

function parseLogLineRecord(lineNo: number, line: string): Record<string, any> {
  const mApache = line.match(APACHE_LOG_RE);
  if (mApache) {
    const rb = mApache[7];
    return {
      line_number: lineNo,
      client_ip: mApache[1] || "",
      user: mApache[2] === "-" ? "" : mApache[2] || "",
      timestamp: mApache[3] || "",
      method: mApache[4] || "",
      path: mApache[5] || "",
      status_code: Number(mApache[6]),
      response_bytes: rb && /^\d+$/.test(rb) ? Number(rb) : 0,
      referer: mApache[8] || "",
      user_agent: mApache[9] || "",
    };
  }
  const mTs = line.match(TS_LEVEL_LOG_RE);
  if (mTs) {
    const rest = (mTs[4] || "").trim();
    const rec: Record<string, any> = {
      line_number: lineNo,
      timestamp: mTs[1] || "",
      level: (mTs[2] || "INFO").toUpperCase() === "WARNING" ? "WARN" : (mTs[2] || "INFO").toUpperCase(),
      logger: mTs[3] || "",
      message: rest,
    };
    let match: RegExpExecArray | null;
    KV_PAIR_RE.lastIndex = 0;
    while ((match = KV_PAIR_RE.exec(rest)) !== null) {
      const key = match[1].toLowerCase().replace(/[^a-z0-9_]+/g, "_");
      if (["line_number", "timestamp", "level", "logger", "message"].includes(key)) continue;
      const val = match[2].replace(/^"|"$/g, "");
      if (val.endsWith("ms") && /^\d+(\.\d+)?$/.test(val.slice(0, -2))) {
        rec[key.endsWith("_ms") ? key : `${key}_ms`] = Number(val.slice(0, -2));
      } else if (/^-?\d+(\.\d+)?$/.test(val)) {
        rec[key] = Number(val);
      } else {
        rec[key] = val;
      }
    }
    return rec;
  }
  let level = "INFO";
  for (const lvl of ["CRITICAL", "FATAL", "ERROR", "WARN", "WARNING", "DEBUG", "TRACE"]) {
    if (new RegExp(`\\b${lvl}\\b`, "i").test(line)) {
      level = lvl === "WARNING" ? "WARN" : lvl;
      break;
    }
  }
  return { line_number: lineNo, level, message: line };
}

function parseLogOrTextToMatrix(text: string, ext: string): { matrix: any[][]; label: string } {
  const cleaned = text.replace(/^\uFEFF/, "");
  const nonEmpty = cleaned.split(/\r?\n/).filter((l) => l.trim().length > 0);
  if (nonEmpty.length === 0) return { matrix: [], label: ext.toUpperCase() };

  // 1. JSON Lines (.jsonl / .ndjson / JSON objects per line)
  if (ext === "jsonl" || ext === "ndjson" || nonEmpty[0].trimStart().startsWith("{")) {
    const objs: Record<string, any>[] = [];
    const keys: string[] = [];
    const keySet = new Set<string>();
    for (let i = 0; i < nonEmpty.length; i++) {
      const ln = nonEmpty[i].trim();
      let obj: Record<string, any>;
      try {
        const parsed = JSON.parse(ln);
        obj = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : { value: parsed };
      } catch {
        obj = parseLogLineRecord(i + 1, ln);
      }
      objs.push(obj);
      for (const k of Object.keys(obj)) {
        if (!keySet.has(k)) {
          keySet.add(k);
          keys.push(k);
        }
      }
    }
    return {
      matrix: [keys, ...objs.map((o) => keys.map((k) => o[k] ?? ""))],
      label: "JSONL",
    };
  }

  // 2. Structured Apache/Nginx/Timestamped/Syslog lines
  const looksLikeStructuredLog =
    ext === "log" ||
    nonEmpty.slice(0, 5).some((ln) => APACHE_LOG_RE.test(ln) || TS_LEVEL_LOG_RE.test(ln));

  if (looksLikeStructuredLog) {
    // Check if delimited .log/.txt first (only if not matching Apache/Timestamped)
    const hasPatternMatch = nonEmpty.slice(0, 5).some((ln) => APACHE_LOG_RE.test(ln) || TS_LEVEL_LOG_RE.test(ln));
    if (!hasPatternMatch) {
      const first = nonEmpty[0];
      for (const delim of ["\t", ",", "|", ";"]) {
        const c0 = first.split(delim).length;
        if (c0 >= 3 && nonEmpty.length >= 2 && nonEmpty.slice(1, 5).every((l) => Math.abs(l.split(delim).length - c0) <= 1)) {
          return { matrix: parseDelimitedText(cleaned, delim), label: ext.toUpperCase() };
        }
      }
    }
    const recs: Record<string, any>[] = [];
    const keys: string[] = [];
    const keySet = new Set<string>();
    for (let i = 0; i < nonEmpty.length; i++) {
      const rec = parseLogLineRecord(i + 1, nonEmpty[i]);
      recs.push(rec);
      for (const k of Object.keys(rec)) {
        if (!keySet.has(k)) {
          keySet.add(k);
          keys.push(k);
        }
      }
    }
    return {
      matrix: [keys, ...recs.map((r) => keys.map((k) => r[k] ?? ""))],
      label: "LOG",
    };
  }

  // 3. Delimited .txt
  return { matrix: parseDelimitedText(cleaned), label: ext.toUpperCase() };
}

function makeUniqueFieldName(rawHeader: string, idx: number, used: Set<string>): string {
  const ascii = rawHeader
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
  let base = ascii && ascii.length >= 2 ? ascii : `col_${idx + 1}`;
  if (/^\d/.test(base)) base = `col_${base}`;
  let candidate = base;
  let n = 2;
  while (used.has(candidate)) {
    candidate = `${base}_${n++}`;
  }
  used.add(candidate);
  return candidate;
}

function parseDelimitedText(text: string, forcedDelimiter?: string): string[][] {
  const cleaned = text.replace(/^\uFEFF/, "");
  const firstLine = cleaned.split(/\r?\n/, 1)[0] || "";
  const delimiter =
    forcedDelimiter ||
    (firstLine.includes("\t")
      ? "\t"
      : firstLine.split(";").length > firstLine.split(",").length
      ? ";"
      : ",");

  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < cleaned.length; i++) {
    const ch = cleaned[i];
    if (inQuotes) {
      if (ch === '"') {
        if (cleaned[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      continue;
    }

    if (ch === '"') {
      inQuotes = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\r") {
      if (cleaned[i + 1] === "\n") i++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else if (ch === "\n") {
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += ch;
    }
  }
  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

export async function parseSpreadsheetBuffer(
  buffer: ArrayBuffer | Uint8Array,
  filename: string,
  options: {
    sheetName?: string;
    hasHeader?: boolean;
    nullStrategy?: string;
    deduplicate?: boolean;
    columnRenames?: Record<string, string>;
    typeOverrides?: Record<string, string>;
  } = {}
): Promise<ParsedExcelResult> {
  const ext = (filename.split(".").pop() || "").toLowerCase();
  const hasHeader = options.hasHeader !== false;
  const nullStrategy = options.nullStrategy || "keep";
  const deduplicate = Boolean(options.deduplicate);
  const columnRenames = options.columnRenames || {};
  const typeOverrides = options.typeOverrides || {};

  let rawMatrix: any[][] = [];
  let sheets: string[] = [];
  let selectedSheet = options.sheetName || "";

  if (ext === "json") {
    const text = new TextDecoder("utf-8").decode(buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer));
    const parsed = JSON.parse(text);
    const arr = Array.isArray(parsed) ? parsed : Array.isArray(parsed?.data) ? parsed.data : [parsed];
    if (arr.length > 0 && typeof arr[0] === "object") {
      const keySet = new Set<string>();
      const keys: string[] = [];
      for (const r of arr) {
        if (r && typeof r === "object") {
          for (const k of Object.keys(r)) {
            if (!keySet.has(k)) {
              keySet.add(k);
              keys.push(k);
            }
          }
        }
      }
      rawMatrix = [keys, ...arr.map((r: any) => keys.map((k) => r?.[k] ?? ""))];
    }
    sheets = ["JSON"];
    selectedSheet = "JSON";
  } else if (ext === "jsonl" || ext === "ndjson" || ext === "log" || ext === "txt") {
    const text = new TextDecoder("utf-8").decode(buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer));
    const parsed = parseLogOrTextToMatrix(text, ext);
    rawMatrix = parsed.matrix;
    sheets = [parsed.label];
    selectedSheet = parsed.label;
  } else if (ext === "csv" || ext === "tsv") {
    const text = new TextDecoder("utf-8").decode(buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer));
    rawMatrix = parseDelimitedText(text, ext === "tsv" ? "\t" : undefined);
    sheets = [ext.toUpperCase()];
    selectedSheet = ext.toUpperCase();
  } else if (ext === "xls") {
    throw new Error(
      "Legacy binary .xls files require server-side upload or saving as .xlsx/.csv in browser preview mode."
    );
  } else {
    const inputBuffer = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
    const parsedSheets = await readXlsxFile(inputBuffer);
    sheets = parsedSheets.map((s) => s.sheet);
    const targetSheet =
      (selectedSheet ? parsedSheets.find((s) => s.sheet === selectedSheet) : undefined) || parsedSheets[0];
    selectedSheet = targetSheet?.sheet || "";
    rawMatrix = (targetSheet?.data || []).map((row) =>
      row.map((cell) => (cell === null || cell === undefined ? "" : cell))
    );
  }

  // Filter completely empty trailing/leading rows
  rawMatrix = rawMatrix.filter((row) => Array.isArray(row) && row.some((cell) => String(cell ?? "").trim() !== ""));

  if (rawMatrix.length === 0) {
    return {
      sheets,
      selected_sheet: selectedSheet,
      total_rows: 0,
      duplicate_count: 0,
      columns: [],
      preview_rows: [],
      all_rows: [],
    };
  }

  const maxCols = rawMatrix.reduce((mx, r) => Math.max(mx, r.length), 0);
  const headerRow = hasHeader
    ? Array.from({ length: maxCols }, (_, i) => String(rawMatrix[0][i] ?? "").trim() || `ستون ${i + 1}`)
    : Array.from({ length: maxCols }, (_, i) => `ستون ${i + 1}`);
  const dataMatrix = hasHeader ? rawMatrix.slice(1) : rawMatrix;

  const usedNames = new Set<string>();
  const colDefs = headerRow.map((rawHdr, idx) => {
    const displayBase = columnRenames[rawHdr] || columnRenames[`col_${idx + 1}`] || rawHdr;
    const fieldName = makeUniqueFieldName(displayBase, idx, usedNames);
    const finalDisplay = columnRenames[fieldName] || displayBase;
    return { idx, rawHdr, fieldName, displayName: finalDisplay };
  });

  // Build raw records
  let records: Record<string, any>[] = dataMatrix.map((row) => {
    const obj: Record<string, any> = {};
    for (const c of colDefs) {
      let val = row[c.idx];
      if (val instanceof Date && !Number.isNaN(val.getTime())) {
        val = val.toISOString().slice(0, 10);
      } else if (typeof val === "string") {
        val = val.trim();
      }
      obj[c.fieldName] = val === undefined || val === null ? "" : val;
    }
    return obj;
  });

  // Null strategy
  if (nullStrategy === "drop_rows") {
    records = records.filter((r) => colDefs.every((c) => r[c.fieldName] !== "" && r[c.fieldName] !== null && r[c.fieldName] !== undefined));
  }

  // Duplicate detection & optional deduplication
  const seen = new Set<string>();
  let duplicateCount = 0;
  const deduped: Record<string, any>[] = [];
  for (const r of records) {
    const sig = JSON.stringify(r);
    if (seen.has(sig)) {
      duplicateCount++;
      if (!deduplicate) deduped.push(r);
    } else {
      seen.add(sig);
      deduped.push(r);
    }
  }
  records = deduped;

  // Infer column types and cast values cleanly without allocating full-column temporary copies
  const columns: ParsedExcelColumn[] = colDefs.map((c) => {
    const sampleNonNull: any[] = [];
    let nullable = false;
    for (let i = 0; i < records.length; i++) {
      const v = records[i][c.fieldName];
      if (v === "" || v === null || v === undefined) {
        nullable = true;
      } else if (sampleNonNull.length < 150) {
        sampleNonNull.push(v);
      }
      if (nullable && sampleNonNull.length >= 150) break;
    }
    const ov = typeOverrides[c.fieldName] || typeOverrides[c.rawHdr];
    const dtype = inferType(sampleNonNull, ov);
    const semanticRole = inferSemanticRole(c.displayName, dtype);
    return {
      field_name: c.fieldName,
      original_name: c.rawHdr,
      display_name: c.displayName,
      data_type: dtype,
      semantic_role: semanticRole,
      nullable,
    };
  });

  // Compute numeric means for fill_mean if selected
  const means: Record<string, number> = {};
  if (nullStrategy === "fill_mean") {
    for (const col of columns) {
      if (col.data_type === "integer" || col.data_type === "float") {
        const nums = records.map((r) => tryParseNumber(r[col.field_name])).filter((x): x is number => x !== null);
        means[col.field_name] = nums.length ? Math.round((nums.reduce((a, b) => a + b, 0) / nums.length) * 100) / 100 : 0;
      }
    }
  }

  const typedRows = records.map((r) => {
    const out: Record<string, any> = {};
    for (const col of columns) {
      const rawVal = r[col.field_name];
      const isEmpty = rawVal === "" || rawVal === null || rawVal === undefined;
      if (col.data_type === "integer" || col.data_type === "float") {
        if (isEmpty) {
          out[col.field_name] =
            nullStrategy === "fill_zero" ? 0 : nullStrategy === "fill_mean" ? means[col.field_name] ?? 0 : null;
        } else {
          const parsed = tryParseNumber(rawVal);
          out[col.field_name] =
            parsed !== null ? (col.data_type === "integer" ? Math.round(parsed) : parsed) : 0;
        }
      } else if (col.data_type === "boolean") {
        if (isEmpty) out[col.field_name] = false;
        else out[col.field_name] = String(rawVal).toLowerCase() === "true" || rawVal === true || rawVal === 1;
      } else {
        if (isEmpty) {
          out[col.field_name] = nullStrategy === "fill_zero" ? "نامشخص" : "";
        } else {
          out[col.field_name] = String(rawVal);
        }
      }
    }
    return out;
  });

  return {
    sheets,
    selected_sheet: selectedSheet,
    total_rows: typedRows.length,
    duplicate_count: duplicateCount,
    columns,
    preview_rows: typedRows.slice(0, 50),
    all_rows: typedRows,
  };
}
